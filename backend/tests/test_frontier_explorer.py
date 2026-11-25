"""Verification tests for the Analyze Re-inspired Layer-Level Optimization Frontier Explorer."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_optimizer_layers_catalog_lists_individual_layers():
    r = client.get("/api/optimizer/layers?view=ADJUSTED&mode=FORWARD_PLANNING")
    assert r.status_code == 200
    layers = r.json()
    # Farmers has 2 distinct layers (Occ XL + Agg XL), Pacific EQ 1, EuroWind 1, plus Retro ILW = >=5
    assert len(layers) >= 5
    keys = {l["layerKey"] for l in layers}
    assert "prog-farmers-us-27::lyr-occ-1" in keys
    assert "prog-farmers-us-27::lyr-agg-1" in keys
    assert "cand-retro-ilw-fl" in keys


def test_frontier_explorer_layer_bounds_lock_and_custom_constraints():
    # Lock Farmers Occ XL at 50%, cap Farmers Agg XL at 80%, add EL/Capital cap and 1-in-500 TVaR cap
    r = client.post(
        "/api/optimizer/explore?num_points=8",
        json={
            "mode": "FORWARD_PLANNING",
            "granularity": "LAYER",
            "objective": "MAX_EXPECTED_PROFIT",
            "view": "ADJUSTED",
            "totalCapital": 160.0,
            "maxDeployedCollateral": 160.0,
            "maxTvar100M": 95.0,
            "maxTvar250M": 115.0,
            "maxElCapitalRatio": 0.30,
            "regionPerilCaps": [{"regionPerilKey": "US-FL-WS", "maxLimitM": 75.0}],
            "tvarCaps": [{"returnPeriod": 500, "maxTvarM": 130.0}],
            "layerBounds": [
                {"layerKey": "prog-farmers-us-27::lyr-occ-1", "minShare": 0.5, "maxShare": 0.5, "locked": False},
                {"layerKey": "prog-farmers-us-27::lyr-agg-1", "minShare": 0.0, "maxShare": 0.8, "locked": False},
            ],
            "includeRetroIlwCandidate": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["frontier"]) == 8
    assert body["initialPortfolio"]["tvar250M"] > 0
    rec = body["frontier"][body["recommendedIndex"]]
    assert rec["status"] in ("Optimal", "Feasible")

    by_key = {l["layerKey"]: l for l in rec["layers"]}
    assert abs(by_key["prog-farmers-us-27::lyr-occ-1"]["optimalShare"] - 0.5) < 1e-3
    assert by_key["prog-farmers-us-27::lyr-agg-1"]["optimalShare"] <= 0.8001

    # Return-period Euler stack has 10 points and sums to totalLossM
    assert len(rec["returnPeriodContributions"]) == 10
    rp250 = next(x for x in rec["returnPeriodContributions"] if x["returnPeriod"] == 250.0)
    assert abs(sum(rp250["byLayer"].values()) - rp250["totalLossM"]) < 0.05

    # Constraints include EL/Capital and custom 1-in-500 TVaR
    c_names = [c["name"] for c in rec["constraints"]]
    assert any("EL / Capital" in n for n in c_names)
    assert any("1-in-500" in n for n in c_names)
