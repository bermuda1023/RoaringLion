"""Tests for Return / Risk ratio metrics, monotone tail-balanced frontier, and Max Return / TVaR objectives."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BASE_PAYLOAD = {
    "mode": "FORWARD_PLANNING",
    "granularity": "LAYER",
    "objective": "MAX_EXPECTED_PROFIT",
    "view": "ADJUSTED",
    "totalCapital": 160.0,
    "maxDeployedCollateral": 160.0,
    "maxTvar100M": 95.0,
    "maxTvar250M": 115.0,
    "maxElCapitalRatio": 0.35,
    "regionPerilCaps": [{"regionPerilKey": "US-FL-WS", "maxLimitM": 75.0}],
    "tvarCaps": [],
    "layerBounds": [],
    "minLineSizeShare": 0.0,
    "includeRetroIlwCandidate": True,
    "retroIlwLimitM": 25.0,
    "retroIlwTriggerBn": 40.0,
}


def test_frontier_explorer_exposes_return_risk_ratios_and_is_monotone_without_dips():
    r = client.post("/api/optimizer/explore?num_points=12", json=BASE_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    frontier = body["frontier"]
    assert len(frontier) == 12

    # Check Return / Risk ratio fields on initial portfolio and every frontier point
    for snap in [body["initialPortfolio"], *(p["metrics"] for p in frontier)]:
        assert snap["stdDevM"] > 0
        assert abs(snap["returnToTvar250"] - snap["riskRewardRatio"]) < 1e-4
        assert abs(snap["returnToTvar100"] - (snap["expectedProfitM"] / snap["tvar100M"])) < 1e-3
        assert abs(snap["returnToTvar250"] - (snap["expectedProfitM"] / snap["tvar250M"])) < 1e-3
        assert abs(snap["returnToTvar500"] - (snap["expectedProfitM"] / snap["tvar500M"])) < 1e-3
        assert abs(snap["returnToTvar1000"] - (snap["expectedProfitM"] / snap["tvar1000M"])) < 1e-3
        assert abs(snap["returnToStdDev"] - (snap["expectedProfitM"] / snap["stdDevM"])) < 2e-3

    # Balanced tail scenario selection eliminates non-monotonic TVaR-250 dips:
    # sorted by actual TVaR-250, expected return must be monotonically non-decreasing.
    feasible = [p for p in frontier if p["status"] in ("Optimal", "Feasible")]
    assert len(feasible) >= 10
    by_tvar = sorted(feasible, key=lambda p: (p["metrics"]["tvar250M"], p["metrics"]["expectedProfitM"]))
    for a, b in zip(by_tvar, by_tvar[1:]):
        assert b["metrics"]["expectedProfitM"] >= a["metrics"]["expectedProfitM"] - 0.05
    # Actual TVaR-250 must closely honor the target cap (no large tail-scenario underestimate)
    for p in feasible:
        assert p["metrics"]["tvar250M"] <= p["targetTvar250M"] * 1.06 + 0.5

    # Peak indices point to the maximum-ratio feasible point for each horizon
    for field, metric in [
        ("peakReturnToTvar100Index", "returnToTvar100"),
        ("peakReturnToTvar250Index", "returnToTvar250"),
        ("peakReturnToTvar1000Index", "returnToTvar1000"),
        ("peakReturnToStdDevIndex", "returnToStdDev"),
    ]:
        pk_idx = body[field]
        assert 0 <= pk_idx < len(frontier)
        pk_val = frontier[pk_idx]["metrics"][metric]
        max_val = max(p["metrics"][metric] for p in feasible)
        assert pk_val >= max_val - 1e-3


def test_single_point_solve_max_return_on_tvar_objectives_respect_constraints():
    base = client.post("/api/optimizer/solve", json=BASE_PAYLOAD)
    assert base.status_code == 200
    base_body = base.json()
    assert base_body["afterReturnToTvar250"] > 0

    for obj, field in [
        ("MAX_RETURN_ON_TVAR_250", "afterReturnToTvar250"),
        ("MAX_RETURN_ON_TVAR_100", "afterReturnToTvar100"),
        ("MAX_RETURN_ON_TVAR_1000", "afterReturnToTvar1000"),
    ]:
        r = client.post("/api/optimizer/solve", json={**BASE_PAYLOAD, "objective": obj})
        assert r.status_code == 200
        body = r.json()
        assert body["objective"] == obj
        assert body["status"] in ("Optimal", "Feasible")
        # Non-degenerate knee portfolio (does not collapse to w=0)
        assert body["afterExpectedProfitM"] > 5.0
        assert body["afterTvar250M"] > 0.0
        # Strictly beats or matches max-expected-profit on its own Return / TVaR horizon
        assert body[field] >= base_body[field] - 1e-3
        # Honors TVaR-250 cap & FL cap constraints
        assert body["afterTvar250M"] <= BASE_PAYLOAD["maxTvar250M"] * 1.02
        fl_limit = sum(
            d["optimalLimitM"]
            for d in body["deals"]
            if d["direction"] == "ASSUMED" and "farmers" in d["programmeId"]
        )
        assert fl_limit <= 75.5

    # Also verify fractional Dinkelbach works with MILP minLineSizeShare + locked layer
    r_milp = client.post(
        "/api/optimizer/solve",
        json={
            **BASE_PAYLOAD,
            "objective": "MAX_RETURN_ON_TVAR_250",
            "minLineSizeShare": 0.15,
            "layerBounds": [
                {"layerKey": "prog-farmers-us-27::lyr-occ-1", "minShare": 0.4, "maxShare": 0.4, "locked": False}
            ],
        },
    )
    assert r_milp.status_code == 200
    milp_body = r_milp.json()
    assert milp_body["status"] in ("Optimal", "Feasible")
    assert "MILP" in milp_body["solver"]
    occ_row = next(d for d in milp_body["deals"] if d["layerKey"] == "prog-farmers-us-27::lyr-occ-1")
    assert abs(occ_row["optimalShare"] - 0.4) < 1e-3


def test_frontier_explorer_ratio_objectives_recommend_peak_point():
    for obj, peak_field, ratio_metric, solve_field in [
        ("MAX_RETURN_ON_TVAR_250", "peakReturnToTvar250Index", "returnToTvar250", "afterReturnToTvar250"),
        ("MAX_RETURN_ON_TVAR_100", "peakReturnToTvar100Index", "returnToTvar100", "afterReturnToTvar100"),
        ("MAX_RETURN_ON_TVAR_1000", "peakReturnToTvar1000Index", "returnToTvar1000", "afterReturnToTvar1000"),
    ]:
        r_solve = client.post("/api/optimizer/solve", json={**BASE_PAYLOAD, "objective": obj})
        assert r_solve.status_code == 200
        solve_body = r_solve.json()
        assert solve_body["manifest"]["parameters"]["objective"] == obj

        r = client.post("/api/optimizer/explore?num_points=8", json={**BASE_PAYLOAD, "objective": obj})
        assert r.status_code == 200
        body = r.json()
        assert body["manifest"]["parameters"]["objective"] == obj
        rec = body["frontier"][body["recommendedIndex"]]
        assert body["recommendedIndex"] == body[peak_field]
        # /explore matches /solve's fractional LP Return / TVaR ratio on all horizons
        assert abs(rec["metrics"][ratio_metric] - solve_body[solve_field]) < 5e-3


def test_frontier_explorer_recommended_point_respects_tight_tvar_caps():
    r_250 = client.post(
        "/api/optimizer/explore?num_points=8",
        json={**BASE_PAYLOAD, "objective": "MAX_RETURN_ON_TVAR_250", "maxTvar250M": 35.0},
    )
    assert r_250.status_code == 200
    b250 = r_250.json()
    rec250 = b250["frontier"][b250["recommendedIndex"]]
    assert rec250["metrics"]["tvar250M"] <= 35.0 * 1.03 + 0.5

    r_100 = client.post(
        "/api/optimizer/explore?num_points=8",
        json={**BASE_PAYLOAD, "objective": "MAX_RETURN_ON_TVAR_100", "maxTvar100M": 35.0},
    )
    assert r_100.status_code == 200
    b100 = r_100.json()
    rec100 = b100["frontier"][b100["recommendedIndex"]]
    assert rec100["metrics"]["tvar100M"] <= 35.0 * 1.03 + 0.5

    r_ra = client.post(
        "/api/optimizer/explore?num_points=8",
        json={**BASE_PAYLOAD, "objective": "MAX_RISK_ADJUSTED_RETURN", "maxTvar250M": 40.0},
    )
    assert r_ra.status_code == 200
    bra = r_ra.json()
    rec_ra = bra["frontier"][bra["recommendedIndex"]]
    assert rec_ra["metrics"]["tvar250M"] <= 40.0 * 1.03 + 0.5


def test_efficient_frontier_endpoint_exposes_return_to_tvar_250():
    r = client.post("/api/optimizer/frontier?num_points=6", json=BASE_PAYLOAD)
    assert r.status_code == 200
    pts = r.json()
    assert len(pts) == 6
    for p in pts:
        if p["actualTvar250M"] > 0:
            expected = p["expectedProfitM"] / p["actualTvar250M"]
            assert abs(p["returnToTvar250"] - expected) < 1e-3
