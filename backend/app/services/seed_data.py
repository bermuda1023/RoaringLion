"""Seed data for the Phase 0 First Slice (synthetic 100k-trial catalogue + 3 bound programmes)."""

from __future__ import annotations

from app.engine.sim_generator import REGION_PERILS, YeltTable, generate_synthetic_sim_set
from app.models.domain import (
    CurvePoint,
    GraphEdgeSpec,
    GraphNodeSpec,
    GraphSpec,
    ProgrammeDeal,
    UpliftRule,
    UpliftSet,
)
from app.models.enums import NodeType, PerilCode, ProgrammeStatus
from app.services.lineage import canonical_hash


def _build_farmers_programme(sim_set_id: str) -> ProgrammeDeal:
    uplift_rules = [
        UpliftRule(
            region_peril_key="US-FL-WS",
            flat_severity_factor=1.10,
            curve_points=[
                CurvePoint(loss_threshold=20.0, factor=1.18),
                CurvePoint(loss_threshold=100.0, factor=1.08),
                CurvePoint(loss_threshold=250.0, factor=1.00),
            ],
            additive_load=0.0,
            note="FL Wind house view: +10% demand surge + working-layer severity curve uplift",
        ),
        UpliftRule(
            region_peril_key="US-TX-WS",
            flat_severity_factor=1.06,
            curve_points=[],
            additive_load=0.0,
            note="TX coastal exposure growth load",
        ),
    ]
    uplift_set = UpliftSet(
        uplift_set_id="upl-farmers-us-27",
        name="Farmers US 2027 House Uplift",
        version=1,
        rules=uplift_rules,
        content_hash=canonical_hash([r.model_dump() for r in uplift_rules]),
    )

    nodes = [
        GraphNodeSpec(
            node_id="src-1",
            node_type=NodeType.LOSS_SOURCE,
            label="Loss Source (SYNTH_CAT_V1)",
            params={"simSetId": sim_set_id},
            position={"x": 40, "y": 140},
        ),
        GraphNodeSpec(
            node_id="flt-us-wind",
            node_type=NodeType.REGION_PERIL_FILTER,
            label="Zone Peril: US Wind (FL/TX/SE)",
            params={"regionPerils": ["US-FL-WS", "US-TX-WS", "US-SE-WS"]},
            position={"x": 300, "y": 140},
        ),
        GraphNodeSpec(
            node_id="lyr-occ-1",
            node_type=NodeType.OCC_XL,
            label="1st Cat XL — 50m xs 80m (1@100%)",
            params={
                "occLimit": 50.0,
                "occAttachment": 80.0,
                "occFranchise": 0.0,
                "reinstatements": 1.0,
                "ripRate": 1.0,
                "depositPremium": 11.5,
                "placedShare": 1.0,
            },
            position={"x": 600, "y": 40},
        ),
        GraphNodeSpec(
            node_id="lyr-agg-1",
            node_type=NodeType.AGG_XL,
            label="Annual Agg XL — 35m xs 95m",
            params={
                "aggLimit": 35.0,
                "aggAttachment": 95.0,
                "perEventFranchise": 10.0,
                "perEventCap": 60.0,
                "depositPremium": 7.8,
                "placedShare": 1.0,
            },
            position={"x": 600, "y": 240},
        ),
        GraphNodeSpec(
            node_id="out-pricing-occ",
            node_type=NodeType.PRICING_OUTPUT,
            label="Pricing Output (Occ XL)",
            params={},
            position={"x": 920, "y": 40},
        ),
        GraphNodeSpec(
            node_id="out-yelt-agg",
            node_type=NodeType.YELT_OUTPUT,
            label="YELT Output (Agg XL)",
            params={},
            position={"x": 920, "y": 240},
        ),
    ]
    edges = [
        GraphEdgeSpec(edge_id="e1", source="src-1", target="flt-us-wind", sign=1, weight=1.0),
        GraphEdgeSpec(edge_id="e2", source="flt-us-wind", target="lyr-occ-1", sign=1, weight=1.0),
        GraphEdgeSpec(edge_id="e3", source="flt-us-wind", target="lyr-agg-1", sign=1, weight=1.0),
        GraphEdgeSpec(edge_id="e4", source="lyr-occ-1", target="out-pricing-occ", sign=1, weight=1.0),
        GraphEdgeSpec(edge_id="e5", source="lyr-agg-1", target="out-yelt-agg", sign=1, weight=1.0),
    ]
    graph = GraphSpec(
        graph_id="grp-farmers-us-27",
        name="Farmers US Wind Tower 2027",
        version=1,
        currency="USD",
        nodes=nodes,
        edges=edges,
        content_hash=canonical_hash({"nodes": [n.model_dump() for n in nodes], "edges": [e.model_dump() for e in edges]}),
    )

    return ProgrammeDeal(
        programme_id="prog-farmers-us-27",
        cedent_name="Farmers Group",
        programme_name="US Wind Occ + Agg Tower",
        treaty_year=2027,
        renewal_period="J27",
        office="BDA",
        underwriter="Lead Underwriter",
        broker="Broker A",
        status=ProgrammeStatus.BOUND,
        currency="USD",
        sim_set_id=sim_set_id,
        uplift_set=uplift_set,
        graph=graph,
        written_line=1.0,
        signed_line=1.0,
        deposit_premium=19.3,
        limit=85.0,
        rol=0.2271,
        collateral_required=85.0,
        region_perils=["US-FL-WS", "US-TX-WS", "US-SE-WS"],
        perils=[PerilCode.WS],
    )


def _build_pacific_eq_programme(sim_set_id: str) -> ProgrammeDeal:
    uplift_rules = [
        UpliftRule(
            region_peril_key="US-CA-EQ",
            flat_severity_factor=1.08,
            curve_points=[],
            additive_load=0.0,
            note="CA EQ fire-following load",
        ),
        UpliftRule(
            region_peril_key="JP-KANTO-EQ",
            flat_severity_factor=1.05,
            curve_points=[],
            additive_load=0.0,
            note="Kanto liquefaction adjustment",
        ),
    ]
    uplift_set = UpliftSet(
        uplift_set_id="upl-pacific-eq-27",
        name="Pacific EQ 2027 Uplift",
        version=1,
        rules=uplift_rules,
        content_hash=canonical_hash([r.model_dump() for r in uplift_rules]),
    )
    nodes = [
        GraphNodeSpec(node_id="src-eq", node_type=NodeType.LOSS_SOURCE, label="Loss Source", params={"simSetId": sim_set_id}, position={"x": 40, "y": 100}),
        GraphNodeSpec(node_id="flt-eq", node_type=NodeType.REGION_PERIL_FILTER, label="Zone Peril: CA + JP EQ", params={"regionPerils": ["US-CA-EQ", "JP-KANTO-EQ"]}, position={"x": 300, "y": 100}),
        GraphNodeSpec(
            node_id="lyr-eq-1",
            node_type=NodeType.OCC_XL,
            label="Pacific EQ — 45m xs 70m (1@100%)",
            params={
                "occLimit": 45.0,
                "occAttachment": 70.0,
                "occFranchise": 0.0,
                "reinstatements": 1.0,
                "ripRate": 1.0,
                "depositPremium": 7.2,
                "placedShare": 1.0,
            },
            position={"x": 600, "y": 100},
        ),
        GraphNodeSpec(node_id="out-eq", node_type=NodeType.YELT_OUTPUT, label="YELT Output", params={}, position={"x": 920, "y": 100}),
    ]
    edges = [
        GraphEdgeSpec(edge_id="eq1", source="src-eq", target="flt-eq", sign=1, weight=1.0),
        GraphEdgeSpec(edge_id="eq2", source="flt-eq", target="lyr-eq-1", sign=1, weight=1.0),
        GraphEdgeSpec(edge_id="eq3", source="lyr-eq-1", target="out-eq", sign=1, weight=1.0),
    ]
    graph = GraphSpec(
        graph_id="grp-pacific-eq-27",
        name="Pacific Rim EQ 2027",
        version=1,
        currency="USD",
        nodes=nodes,
        edges=edges,
        content_hash=canonical_hash({"nodes": [n.model_dump() for n in nodes], "edges": [e.model_dump() for e in edges]}),
    )
    return ProgrammeDeal(
        programme_id="prog-pacific-eq-27",
        cedent_name="Pacific Re",
        programme_name="CA & Japan EQ Cat XL",
        treaty_year=2027,
        renewal_period="AP27",
        office="BDA",
        underwriter="Lead Underwriter",
        broker="Broker B",
        status=ProgrammeStatus.BOUND,
        currency="USD",
        sim_set_id=sim_set_id,
        uplift_set=uplift_set,
        graph=graph,
        written_line=1.0,
        signed_line=1.0,
        deposit_premium=7.2,
        limit=45.0,
        rol=0.16,
        collateral_required=45.0,
        region_perils=["US-CA-EQ", "JP-KANTO-EQ"],
        perils=[PerilCode.EQ],
    )


def _build_eurowind_programme(sim_set_id: str) -> ProgrammeDeal:
    uplift_set = UpliftSet(
        uplift_set_id="upl-eurowind-27",
        name="EuroWind 2027 Flat Load",
        version=1,
        rules=[UpliftRule(region_peril_key="EU-DE-WS", flat_severity_factor=1.04, note="Inflation adjustment")],
        content_hash=canonical_hash({"EU-DE-WS": 1.04}),
    )
    nodes = [
        GraphNodeSpec(node_id="src-eu", node_type=NodeType.LOSS_SOURCE, label="Loss Source", params={"simSetId": sim_set_id}, position={"x": 40, "y": 100}),
        GraphNodeSpec(node_id="flt-eu", node_type=NodeType.REGION_PERIL_FILTER, label="Zone Peril: EU Wind", params={"regionPerils": ["EU-DE-WS"]}, position={"x": 300, "y": 100}),
        GraphNodeSpec(
            node_id="lyr-eu-1",
            node_type=NodeType.OCC_XL,
            label="EuroWind — 30m xs 45m (1@100%)",
            params={
                "occLimit": 30.0,
                "occAttachment": 45.0,
                "occFranchise": 0.0,
                "reinstatements": 1.0,
                "ripRate": 1.0,
                "depositPremium": 4.9,
                "placedShare": 1.0,
            },
            position={"x": 600, "y": 100},
        ),
        GraphNodeSpec(node_id="out-eu", node_type=NodeType.YELT_OUTPUT, label="YELT Output", params={}, position={"x": 920, "y": 100}),
    ]
    edges = [
        GraphEdgeSpec(edge_id="eu1", source="src-eu", target="flt-eu", sign=1, weight=1.0),
        GraphEdgeSpec(edge_id="eu2", source="flt-eu", target="lyr-eu-1", sign=1, weight=1.0),
        GraphEdgeSpec(edge_id="eu3", source="lyr-eu-1", target="out-eu", sign=1, weight=1.0),
    ]
    graph = GraphSpec(
        graph_id="grp-eurowind-27",
        name="EuroWind Core 2027",
        version=1,
        currency="USD",
        nodes=nodes,
        edges=edges,
        content_hash=canonical_hash({"nodes": [n.model_dump() for n in nodes], "edges": [e.model_dump() for e in edges]}),
    )
    return ProgrammeDeal(
        programme_id="prog-eurowind-27",
        cedent_name="Rheinland Mutual",
        programme_name="European Windstorm XL",
        treaty_year=2027,
        renewal_period="J27",
        office="LON",
        underwriter="UW Team B",
        broker="Broker C",
        status=ProgrammeStatus.BOUND,
        currency="USD",
        sim_set_id=sim_set_id,
        uplift_set=uplift_set,
        graph=graph,
        written_line=1.0,
        signed_line=1.0,
        deposit_premium=4.9,
        limit=30.0,
        rol=0.1633,
        collateral_required=30.0,
        region_perils=["EU-DE-WS"],
        perils=[PerilCode.WS],
    )


def create_phase0_state(num_trials: int = 100_000) -> tuple[dict[str, YeltTable], dict[str, ProgrammeDeal]]:
    yelt = generate_synthetic_sim_set(num_trials=num_trials)
    sim_sets = {yelt.sim_set_id: yelt}
    progs = [
        _build_farmers_programme(yelt.sim_set_id),
        _build_pacific_eq_programme(yelt.sim_set_id),
        _build_eurowind_programme(yelt.sim_set_id),
    ]
    return sim_sets, {p.programme_id: p for p in progs}
