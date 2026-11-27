"""Verification suite for the ~42 synthetic reinsurance clients/programmes seeded alongside the core demos."""

from __future__ import annotations

import math
from collections import Counter

from fastapi.testclient import TestClient

from app.engine.ilw import evaluate_ilw_trial_payouts
from app.engine.optimizer import _compute_portfolio_snapshot
from app.engine.sim_generator import REGION_PERILS
from app.engine.slice_runner import evaluate_programme_dag
from app.main import CONTRACT_STORE, SIM_STORE, _base_yelt, _ind_yelt, app
from app.models.enums import ContractDirection, IlwTriggerType, ViewOfRisk
from app.models.phase1 import IlwPriceRequest
from app.services.contracts import (
    _sim_loss_files,
    contract_layers,
    contract_to_programme_deal,
    validate_pricing_links_one_to_one,
)
from app.services.lineage import LineageRegistry
from app.services.loss_files import validate_filters_match_loss_files
from app.services.pricing_bind import validate_pricing_output_single_connection
from app.db.database import get_session
import numpy as np

client = TestClient(app)

CORE_SEED_IDS = {
    "prog-farmers-us-27",
    "prog-farmers-us-26",
    "prog-pacific-eq-27",
    "prog-eurowind-27",
    "prog-ilw-fl-wind-27",
    "prog-retro-unl-27",
}


def test_synthetic_book_seeds_cleanly_with_expected_diversity_and_schema():
    contracts = client.get("/api/contracts").json()
    by_id = {c["programmeId"]: c for c in contracts}

    # Core demo programmes remain intact
    assert CORE_SEED_IDS <= set(by_id)

    synthetic = [c for c in contracts if c["programmeId"].startswith("prog-syn-")]
    assert len(synthetic) == 42

    # Status mix across the seeded book (core + synthetic)
    seeded = [c for c in contracts if c["programmeId"] in CORE_SEED_IDS or c["programmeId"].startswith("prog-syn-")]
    status_counts = Counter(c["status"] for c in seeded)
    assert 25 <= status_counts["BOUND"] <= 35
    for needed in ("BOUND", "QUOTED", "AUTHORIZED", "DECLINED", "NTU", "EXPIRED"):
        assert status_counts[needed] >= 2, f"Expected >=2 seeded deals in status {needed}, got {status_counts[needed]}"

    # Structure & direction diversity
    structures = Counter(c["structure"] for c in seeded)
    for st in ("OCC_XL", "AGG_XL", "QUOTA_SHARE", "CASCADING", "ILW"):
        assert structures[st] >= 3, f"Expected >=3 seeded deals of structure {st}, got {structures[st]}"

    ilws = [c for c in seeded if c["structure"] == "ILW"]
    assert any(not c["isHedge"] for c in ilws), "Expected at least one Long ILW"
    assert any(c["isHedge"] for c in ilws), "Expected at least one Hedge ILW"

    unl_retros = [c for c in seeded if c["isHedge"] and c["structure"] != "ILW"]
    assert len(unl_retros) >= 3

    # Layer counts across OCC_XL towers (single-layer through 4-layer)
    layer_node_types = {"OCC_XL", "AGG_XL", "QUOTA_SHARE", "AGG_QUOTA_SHARE", "SURPLUS_SHARE", "CASCADING_TOWER", "ILW_OPTION"}
    tower_depths = Counter(
        sum(1 for n in c["graph"]["nodes"] if n["nodeType"] in layer_node_types)
        for c in synthetic
        if c["structure"] == "OCC_XL" and not c["isHedge"]
    )
    assert tower_depths[1] >= 2
    assert tower_depths[2] >= 4
    assert tower_depths[3] >= 4
    assert tower_depths[4] >= 2

    # Every region-peril in the expanded catalogue is represented in the seeded book
    covered_rps = {rp for c in seeded for rp in c["regionPerils"]}
    assert set(REGION_PERILS) <= covered_rps

    # Schema compliance: no authorizedLine/technicalRol, valid 1:1 pricing output links, layer RARC present
    all_full = CONTRACT_STORE.list_contracts()
    with get_session() as db:
        sim_files = _sim_loss_files(db)
    for c in synthetic:
        assert "authorizedLine" not in c
        assert "technicalRol" not in c
        assert c["currency"] == "USD"
        assert c["isHedge"] == (c["direction"] in ("EXTERNAL_RETRO", "INTERNAL_CEDED"))

        full = CONTRACT_STORE.get_contract(c["programmeId"])
        assert full is not None
        validate_pricing_output_single_connection(full.graph)
        validate_pricing_links_one_to_one(full.programme_id, full.graph, all_full)
        validate_filters_match_loss_files(full.graph, sim_files, full.sim_set_id)

        layers = contract_layers(full)
        assert len(layers) >= 1
        for lyr in layers:
            assert lyr.rarc_pct is not None
            assert lyr.linked_pricing_node_id is not None

        for node in full.graph.nodes:
            assert "authorizedLine" not in node.params
            assert "technicalRol" not in node.params

    # Prior-year renewal chains
    chained = [c for c in seeded if c.get("priorContractId")]
    assert len(chained) >= 5
    for c in chained:
        chain = client.get(f"/api/contracts/{c['programmeId']}/chain").json()
        assert len(chain) >= 2
        related = client.get(f"/api/programmes/{c['programmeId']}/related").json()
        assert len(related) >= 2


def test_every_synthetic_programme_evaluates_cleanly_in_engine():
    contracts = [c for c in client.get("/api/contracts").json() if c["programmeId"].startswith("prog-syn-")]
    assert len(contracts) == 42

    for c in contracts:
        pid = c["programmeId"]
        r_eval = client.post(f"/api/programmes/{pid}/evaluate")
        assert r_eval.status_code == 200, f"{pid} failed evaluate: {r_eval.text}"
        body = r_eval.json()
        assert "ADJUSTED" in body["views"] and "UNADJUSTED" in body["views"]
        assert body["manifest"]["manifestId"].startswith("man-dag_eval-")
        for view_key in ("ADJUSTED", "UNADJUSTED"):
            for m in body["views"][view_key]:
                assert math.isfinite(m["el"]) and m["el"] >= 0.0
                assert math.isfinite(m["sd"]) and m["sd"] >= 0.0
                if m["nodeType"] == "PRICING_OUTPUT":
                    assert m["pricing"] is not None, f"{pid} pricing output {m['nodeId']} missing pricing economics"
                    assert m["pricing"]["contractId"] == pid


def test_synthetic_book_runs_and_solves_in_optimizer():
    # Candidates endpoint pages cleanly over the ~40-client book
    p1 = client.get("/api/optimizer/candidates?view=ADJUSTED&mode=FORWARD_PLANNING&limit=25&offset=0")
    assert p1.status_code == 200, p1.text
    b1 = p1.json()
    assert b1["universeProgrammes"] >= 30
    assert len(b1["programmeIds"]) == 25
    assert len(b1["layers"]) >= 25

    p2 = client.get("/api/optimizer/candidates?view=ADJUSTED&mode=FORWARD_PLANNING&limit=25&offset=25")
    assert p2.status_code == 200, p2.text
    b2 = p2.json()
    assert len(b2["programmeIds"]) >= 5
    assert not set(b1["programmeIds"]) & set(b2["programmeIds"])

    # Frontier explorer solves cleanly across the full multi-peril book
    r_exp = client.post(
        "/api/optimizer/explore?num_points=8",
        json={
            "mode": "FORWARD_PLANNING",
            "granularity": "LAYER",
            "view": "ADJUSTED",
            "totalCapital": 250.0,
            "maxDeployedCollateral": 450.0,
            "maxTvar100M": 120.0,
            "maxTvar250M": 150.0,
            "includeRetroIlwCandidate": True,
        },
    )
    assert r_exp.status_code == 200, r_exp.text
    exp = r_exp.json()
    assert len(exp["initialLayers"]) >= 55
    assert len(exp["frontier"]) == 8
    rec = exp["frontier"][exp["recommendedIndex"]]
    assert rec["status"] in ("Optimal", "Feasible")
    assert rec["metrics"]["expectedProfitM"] > 0.0
    assert rec["metrics"]["tvar250M"] > 0.0
    rp250 = next(x for x in rec["returnPeriodContributions"] if x["returnPeriod"] == 250.0)
    assert abs(sum(rp250["byLayer"].values()) - rp250["totalLossM"]) < 0.05


def test_evaluate_programme_dag_fast_path_matches_full_evaluation():
    yelt = SIM_STORE.get_yelt(_base_yelt.sim_set_id)
    ind_yelt = SIM_STORE.get_yelt(_ind_yelt.sim_set_id)
    # Cover multi-layer OCC_XL, AGG_XL, QUOTA_SHARE, CASCADING, and ILW
    for pid in ("prog-syn-001", "prog-syn-003", "prog-syn-004", "prog-syn-011", "prog-syn-034"):
        c = CONTRACT_STORE.get_contract(pid)
        assert c is not None
        prog = contract_to_programme_deal(c)
        _, full_vecs = evaluate_programme_dag(
            prog,
            yelt,
            LineageRegistry(persist=False),
            views=[ViewOfRisk.ADJUSTED],
            industry_yelt=ind_yelt,
            compute_metrics=True,
            need_events=True,
        )
        _, fast_vecs = evaluate_programme_dag(
            prog,
            yelt,
            LineageRegistry(persist=False),
            views=[ViewOfRisk.ADJUSTED],
            industry_yelt=ind_yelt,
            compute_metrics=False,
            need_events=False,
        )
        for node in prog.graph.nodes:
            if node.node_type.value in ("OCC_XL", "AGG_XL", "AGG_QUOTA_SHARE", "CASCADING_TOWER", "ILW_OPTION", "PRICING_OUTPUT"):
                np.testing.assert_allclose(
                    fast_vecs["annual"]["ADJUSTED"][node.node_id],
                    full_vecs["annual"]["ADJUSTED"][node.node_id],
                    atol=1e-12,
                )
                np.testing.assert_allclose(
                    fast_vecs["rip"]["ADJUSTED"][node.node_id],
                    full_vecs["rip"]["ADJUSTED"][node.node_id],
                    atol=1e-12,
                )


def test_return_period_stack_disambiguates_duplicate_layer_names():
    n = 1000
    rng = np.random.default_rng(7)
    v1 = rng.exponential(5.0, size=n)
    v2 = rng.exponential(8.0, size=n)
    items = [
        {
            "layer_key": "p1::l1",
            "programme_id": "p1",
            "layer_id": "l1",
            "cedent_name": "Alpha Re",
            "programme_name": "Alpha XL",
            "layer_name": "1st Cat XL — 25m xs 40m",
            "direction": "ASSUMED",
            "status": "BOUND",
            "region_perils": ["US-FL-WS"],
            "current_share": 1.0,
            "limit_1x": 25.0,
            "prem_1x": 5.0,
            "el_1x": float(v1.mean()),
            "profit_1x": 5.0 - float(v1.mean()),
        },
        {
            "layer_key": "p2::l1",
            "programme_id": "p2",
            "layer_id": "l1",
            "cedent_name": "Beta Re",
            "programme_name": "Beta XL",
            "layer_name": "1st Cat XL — 25m xs 40m",
            "direction": "ASSUMED",
            "status": "BOUND",
            "region_perils": ["US-TX-WS"],
            "current_share": 1.0,
            "limit_1x": 25.0,
            "prem_1x": 6.0,
            "el_1x": float(v2.mean()),
            "profit_1x": 6.0 - float(v2.mean()),
        },
    ]
    mat = np.vstack([v1, v2])
    _, _, rp_stack = _compute_portfolio_snapshot(items, mat, np.array([1.0, 1.0]), total_capital=100.0)
    rp250 = next(r for r in rp_stack if r.return_period == 250.0)
    assert len(rp250.by_layer) == 2
    assert abs(sum(rp250.by_layer.values()) - rp250.total_loss_m) < 0.02


def test_ilw_us_all_ws_includes_northeast_wind():
    ind_yelt = SIM_STORE.get_yelt(_ind_yelt.sim_set_id)
    req_all = IlwPriceRequest(
        name="US All Wind $30B",
        industry_sim_set_id=ind_yelt.sim_set_id,
        region_perils=["US-ALL-WS"],
        trigger_type=IlwTriggerType.BINARY,
        trigger_bn=30.0,
        limit_m=25.0,
        rol=0.15,
        direction=ContractDirection.EXTERNAL_RETRO,
    )
    req_three = req_all.model_copy(update={"region_perils": ["US-FL-WS", "US-TX-WS", "US-SE-WS"]})
    p_all, _, _ = evaluate_ilw_trial_payouts(ind_yelt, req_all)
    p_three, _, _ = evaluate_ilw_trial_payouts(ind_yelt, req_three)
    assert float(p_all.mean()) > float(p_three.mean())

