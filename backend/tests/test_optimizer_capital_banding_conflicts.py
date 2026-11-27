"""Tests for:
1. Fund Capital (user-set) vs Deployed Capital (auto-calculated max(0, (limit - premium) * share)) & ceiling enforcement.
2. Hard 100% max share cap across Optimizer catalog, validation, and solver outputs.
3. Structured Single-Cedent Max & Fund Capital conflict detection on /solve and /explore.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.engine.optimizer import OptimizerRequest, detect_constraint_conflicts
from app.engine.pricing_link import deployed_capital_m
from app.main import app

client = TestClient(app)


def test_deployed_capital_formula_unit_and_floors_at_zero():
    assert deployed_capital_m(50.0, 11.5) == pytest.approx(38.5)
    assert deployed_capital_m(50.0 * 0.25, 11.5 * 0.25) == pytest.approx(38.5 * 0.25)
    # Floored at 0 when premium exceeds limit
    assert deployed_capital_m(10.0, 15.0) == 0.0
    # Hedges never require posted collateral
    assert deployed_capital_m(50.0, 5.0, is_hedge=True) == 0.0


def test_portfolio_rollup_auto_calculates_deployed_capital_and_flags_capital_violation():
    r_ok = client.get("/api/portfolio/rollup?view=ADJUSTED&totalCapital=500")
    assert r_ok.status_code == 200, r_ok.text
    ok = r_ok.json()

    expected_deployed = sum(
        max(0.0, row["limit"] - row["premium"])
        for row in ok["layerAllocations"]
        if not row["isHedge"]
    )
    gross_limit = sum(row["limit"] for row in ok["layerAllocations"] if not row["isHedge"])
    assert expected_deployed > 0.0
    assert expected_deployed < gross_limit
    assert ok["deployedCollateral"] == pytest.approx(expected_deployed, abs=1e-2)
    assert ok["capitalExceeded"] is False
    assert ok["capitalHeadroomM"] == pytest.approx(500.0 - expected_deployed, abs=1e-2)
    assert ok["capitalUtilizationPct"] == pytest.approx((expected_deployed / 500.0) * 100.0, abs=0.05)

    # When Fund Capital < Deployed Capital, capitalExceeded is True and headroom is negative
    low_cap = round(expected_deployed * 0.5, 2)
    r_over = client.get(f"/api/portfolio/rollup?view=ADJUSTED&totalCapital={low_cap}")
    assert r_over.status_code == 200, r_over.text
    over = r_over.json()
    assert over["deployedCollateral"] == pytest.approx(expected_deployed, abs=1e-2)
    assert over["capitalExceeded"] is True
    assert over["capitalHeadroomM"] < 0.0
    assert over["capitalUtilizationPct"] > 100.0


def test_optimizer_enforces_fund_capital_ceiling_using_limit_minus_premium():
    # Even if a caller omits maxDeployedCollateral or passes a higher value, Fund Capital caps Deployed Capital.
    r = client.post(
        "/api/optimizer/solve",
        json={
            "mode": "CURRENT",
            "granularity": "LAYER",
            "objective": "MAX_EXPECTED_PROFIT",
            "totalCapital": 120.0,
            "maxDeployedCollateral": 500.0,
            "maxTvar100M": 200.0,
            "maxTvar250M": 250.0,
            "includeRetroIlwCandidate": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in ("Optimal", "Feasible")
    assert body["fundCapitalM"] == pytest.approx(120.0)
    assert body["afterDeployedCapitalM"] <= 120.05

    # Verify deployed capital equals sum(max(0, optimalLimit - optimalPremium)) across assumed layers
    recomputed = sum(
        max(0.0, d["optimalLimitM"] - d["optimalPremiumM"])
        for d in body["deals"]
        if d["direction"] == "ASSUMED"
    )
    assert body["afterDeployedCapitalM"] == pytest.approx(recomputed, abs=0.05)
    # Every optimal share is hard-capped at 100% (1.0)
    assert all(0.0 <= d["optimalShare"] <= 1.0001 for d in body["deals"])

    # Candidates catalog exposes capital1xM = max(0, limit1xM - premium1xM) and defaultMaxShare == 1.0
    cand = client.get("/api/optimizer/candidates?view=ADJUSTED&mode=FORWARD_PLANNING&limit=25&offset=0").json()
    assert cand["universeDeployedCapitalM"] > 0.0
    assert cand["universeMaxLayerLimit1xM"] > 0.0
    assert cand["universeMaxCedentLimit1xM"] >= cand["universeMaxLayerLimit1xM"]
    for lyr in cand["layers"]:
        assert lyr["defaultMaxShare"] == 1.0
        assert lyr["capital1xM"] == pytest.approx(max(0.0, lyr["limit1xM"] - lyr["premium1xM"]), abs=1e-3)
    if cand["retroCandidate"]:
        assert cand["retroCandidate"]["defaultMaxShare"] == 1.0
        assert cand["retroCandidate"]["capital1xM"] == 0.0


def test_optimizer_hard_caps_shares_at_100_percent_validation():
    # maxShare > 1.0 is rejected with 422
    r_bad_max = client.post(
        "/api/optimizer/solve",
        json={
            "mode": "CURRENT",
            "granularity": "LAYER",
            "layerBounds": [
                {"layerKey": "prog-farmers-us-27::lyr-occ-1", "minShare": 0.0, "maxShare": 1.25, "locked": False}
            ],
        },
    )
    assert r_bad_max.status_code == 422

    # maxLineSizeShare > 1.0 is rejected with 422
    r_bad_line = client.post(
        "/api/optimizer/solve",
        json={"mode": "CURRENT", "granularity": "LAYER", "maxLineSizeShare": 1.5},
    )
    assert r_bad_line.status_code == 422

    # minShare > maxShare is rejected with 422
    r_bad_order = client.post(
        "/api/optimizer/solve",
        json={
            "mode": "CURRENT",
            "granularity": "LAYER",
            "layerBounds": [
                {"layerKey": "prog-farmers-us-27::lyr-occ-1", "minShare": 0.6, "maxShare": 0.4, "locked": False}
            ],
        },
    )
    assert r_bad_order.status_code == 422

    # Every layer returned by /api/optimizer/layers has defaultMaxShare <= 1.0
    layers = client.get("/api/optimizer/layers?view=ADJUSTED&mode=FORWARD_PLANNING").json()
    assert len(layers) >= 5
    assert all(l["defaultMaxShare"] <= 1.0 for l in layers)


def test_single_cedent_conflict_detection_returns_structured_details_on_solve_and_explore():
    # Farmers Group has lyr-occ-1 (50m) + lyr-agg-1 (35m) = 85m.
    # Require min 60% on both -> 30m + 21m = 51m minimum required, while Single Cedent Max = 40m -> INFEASIBLE!
    payload = {
        "mode": "CURRENT",
        "granularity": "LAYER",
        "totalCapital": 450.0,
        "maxTvar100M": 200.0,
        "maxTvar250M": 250.0,
        "maxSingleCedentLimitM": 40.0,
        "layerBounds": [
            {"layerKey": "prog-farmers-us-27::lyr-occ-1", "minShare": 0.6, "maxShare": 0.8, "locked": False},
            {"layerKey": "prog-farmers-us-27::lyr-agg-1", "minShare": 0.6, "maxShare": 0.8, "locked": False},
        ],
    }

    r_solve = client.post("/api/optimizer/solve", json=payload)
    assert r_solve.status_code == 200, r_solve.text
    sbody = r_solve.json()
    assert sbody["status"] == "Infeasible"
    err_conflicts = [c for c in sbody["conflicts"] if c["kind"] == "SINGLE_CEDENT_MIN_EXCEEDS_CAP"]
    assert len(err_conflicts) == 1
    c0 = err_conflicts[0]
    assert c0["severity"] == "ERROR"
    assert c0["cedentName"] == "Farmers Group"
    assert c0["capLimitM"] == pytest.approx(40.0)
    assert c0["requiredMinLimitM"] == pytest.approx(51.0)
    assert c0["excessM"] == pytest.approx(11.0)
    assert {ol["layerKey"] for ol in c0["offendingLayers"]} == {
        "prog-farmers-us-27::lyr-occ-1",
        "prog-farmers-us-27::lyr-agg-1",
    }

    r_exp = client.post("/api/optimizer/explore?num_points=4", json=payload)
    assert r_exp.status_code == 200, r_exp.text
    ebody = r_exp.json()
    assert ebody["frontier"][0]["status"] == "Infeasible"
    assert any(
        c["kind"] == "SINGLE_CEDENT_MIN_EXCEEDS_CAP" and c["cedentName"] == "Farmers Group"
        for c in ebody["conflicts"]
    )


def test_single_cedent_cap_exceeding_largest_layer_and_fund_capital_conflict_diagnostics():
    synthetic_items = [
        {
            "layer_key": "p1::l1",
            "programme_id": "p1",
            "layer_id": "l1",
            "cedent_name": "Alpha Re",
            "programme_name": "Alpha Cat",
            "layer_name": "1st XL",
            "direction": "ASSUMED",
            "status": "BOUND",
            "current_share": 0.5,
            "limit_1x": 50.0,
            "prem_1x": 10.0,
            "capital_1x": 40.0,
        },
        {
            "layer_key": "p2::l1",
            "programme_id": "p2",
            "layer_id": "l1",
            "cedent_name": "Beta Re",
            "programme_name": "Beta Cat",
            "layer_name": "1st XL",
            "direction": "ASSUMED",
            "status": "BOUND",
            "current_share": 0.5,
            "limit_1x": 30.0,
            "prem_1x": 5.0,
            "capital_1x": 25.0,
        },
    ]

    # Single Cedent Max ($80m) > largest cedent/layer max ($50m) -> non-binding warning naming Alpha Re
    req_high = OptimizerRequest(total_capital=200.0, max_single_cedent_limit_m=80.0)
    c_high = detect_constraint_conflicts(req_high, synthetic_items)
    assert any(
        c.kind == "SINGLE_CEDENT_CAP_EXCEEDS_LARGEST_MAX"
        and c.severity == "WARNING"
        and c.cedent_name == "Alpha Re"
        and any(ol.layer_key == "p1::l1" for ol in c.offending_layers)
        for c in c_high
    )

    # Single Cedent Max ($40m) feasible, but clips Alpha Re's 100% max ($50m)
    req_clip = OptimizerRequest(total_capital=200.0, max_single_cedent_limit_m=40.0)
    c_clip = detect_constraint_conflicts(req_clip, synthetic_items)
    assert any(
        c.kind == "SINGLE_CEDENT_CLIPS_LAYER_MAX"
        and c.severity == "WARNING"
        and any(ol.layer_key == "p1::l1" for ol in c.offending_layers)
        for c in c_clip
    )

    # Fund Capital ($25m) < min required deployed capital from locked bounds ($20m + $12.5m = $32.5m) -> ERROR
    req_cap = OptimizerRequest(
        total_capital=25.0,
        lock_bound_deals=True,
    )
    c_cap = detect_constraint_conflicts(req_cap, synthetic_items)
    assert any(
        c.kind == "FUND_CAPITAL_MIN_EXCEEDS_CAP"
        and c.severity == "ERROR"
        and c.required_min_limit_m == pytest.approx(32.5)
        and c.excess_m == pytest.approx(7.5)
        for c in c_cap
    )


def test_renew_programme_uses_limit_minus_premium_for_collateral():
    r_ren = client.post(
        "/api/contracts/prog-farmers-us-27/renew",
        json={"treatyYear": 2029, "limit": 90.0, "rol": 0.20, "signedLine": 0.5},
    )
    assert r_ren.status_code == 200, r_ren.text
    ren = r_ren.json()
    try:
        # 90m limit * 20% ROL = 18m prem; at 50% line -> (90 - 18) * 0.5 = 36m deployed capital
        assert ren["collateralRequired"] == pytest.approx(36.0, abs=1e-2)
    finally:
        client.put(f"/api/contracts/{ren['programmeId']}", json={"status": "DECLINED"})
