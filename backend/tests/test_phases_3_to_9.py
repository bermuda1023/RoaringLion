"""Verification Suite for Phases 3, 3b, 4, 5, 6, 7, 8, and 9."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_phase3_gross_net_retro_allocation_conservation():
    client = TestClient(app)
    r = client.post(
        "/api/allocation/gross-net",
        json={
            "view": "ADJUSTED",
            "totalCapital": 160.0,
            "allocationMethod": "TAIL_RELIEF_COTVAR",
            "retroIlwRegionPerils": ["US-FL-WS"],
            "retroIlwTriggerBn": 40.0,
            "retroIlwLimitM": 25.0,
            "retroIlwRol": 0.20,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["retroCostConservationError"] < 1e-9
    assert body["waterfall"]["netTvar250"] < body["waterfall"]["grossTvar250"]
    assert body["waterfall"]["netEffectiveIrrPct"] <= body["waterfall"]["netMeanRocPct"]
    assert len(body["layers"]) >= 3


def test_phase3b_branching_backtest_matrix_and_reverse_stress():
    client = TestClient(app)

    # 1. Create a non-mutating branch cutting Farmers line to 50%
    r_br = client.post(
        "/api/branches",
        json={
            "name": "Cut FL Wind 50%",
            "signedLineOverrides": {"prog-farmers-us-27": 0.5},
        },
    )
    assert r_br.status_code == 200
    bid = r_br.json()["branchId"]

    # 2. Diff branch vs baseline (tail risk must drop!)
    r_diff = client.post(f"/api/branches/{bid}/diff?view=ADJUSTED&totalCapital=160")
    assert r_diff.status_code == 200
    assert r_diff.json()["deltaTvar250"] < 0.0

    # 3. All-direction backtest matrix (baseline vs branch across 1990-2025)
    r_bt = client.post(
        "/api/backtest/matrix",
        json={"branchId": bid, "startYear": 1990, "endYear": 2025, "asIsTrended": True},
    )
    assert r_bt.status_code == 200
    assert len(r_bt.json()["years"]) == 36

    # 4. Reverse Stress Test (auto-forges smallest FL event breaching ROC <= 0%)
    r_rs = client.post(
        "/api/scenarios/reverse-stress",
        json={
            "name": "Auto Reverse Stress ROC<=0%",
            "targetRocPct": 0.0,
            "totalCapital": 160.0,
            "seedRegionPeril": "US-FL-WS",
            "baseGroundUpM": 100.0,
        },
    )
    assert r_rs.status_code == 200
    assert r_rs.json()["scenario"]["provenanceTag"] == "REVERSE_STRESS"
    assert r_rs.json()["requiredGroundUpM"] > 0.0


def test_phase4_highs_optimizer_milp_and_frontier():
    client = TestClient(app)

    # LP solve with shadow prices
    r_lp = client.post(
        "/api/optimizer/solve",
        json={
            "mode": "FORWARD_PLANNING",
            "view": "ADJUSTED",
            "totalCapital": 160.0,
            "maxDeployedCollateral": 160.0,
            "maxTvar100M": 95.0,
            "maxTvar250M": 115.0,
            "includeRetroIlwCandidate": True,
            "regionPerilCaps": [{"regionPerilKey": "US-FL-WS", "maxLimitM": 75.0}],
        },
    )
    assert r_lp.status_code == 200
    lp = r_lp.json()
    assert lp["status"] == "Optimal"
    assert lp["afterTvar250M"] <= 116.5  # full-100k verification matches LP epigraph
    assert len(lp["constraints"]) >= 4

    # MILP solve with semi-continuous minimum line size
    r_milp = client.post(
        "/api/optimizer/solve",
        json={
            "mode": "CURRENT",
            "minLineSizeShare": 0.25,
            "maxTvar250M": 120.0,
        },
    )
    assert r_milp.status_code == 200
    assert r_milp.json()["solver"] == "HiGHS MILP"

    # Pareto Efficient Frontier
    r_fr = client.post(
        "/api/optimizer/frontier?num_points=4",
        json={"maxTvar250M": 120.0, "totalCapital": 160.0},
    )
    assert r_fr.status_code == 200
    assert len(r_fr.json()) == 4


def test_phases_5_to_9_apis_synthetic_ee_pricing_fx_ops():
    client = TestClient(app)

    # Phase 5: Cat Model API Orchestrator (RMS, AIR, KCC) + Synthetic Freq-Sev
    r_rms = client.post(
        "/api/model-runs",
        json={"vendor": "RMS", "programmeId": "prog-farmers-us-27", "edmDatabaseName": "EDM_FARMERS_2027", "numTrials": 20000},
    )
    assert r_rms.status_code == 200
    rms_sim_id = r_rms.json()["simSetId"]
    assert rms_sim_id.startswith("sim-rms-")

    r_syn = client.post(
        "/api/synthetic/generate",
        json={"name": "Cyber Freq-Sev", "regionPerilKey": "US-CA-EQ", "numTrials": 10000, "meanFrequency": 1.5},
    )
    assert r_syn.status_code == 200
    assert r_syn.json()["expectedAnnualLoss"] > 0.0

    # Phase 6: ExposureEclipse bridge
    r_ee = client.get("/api/integrations/exposure-eclipse/programmes")
    assert r_ee.status_code == 200
    assert len(r_ee.json()) >= 2
    r_sync = client.post("/api/integrations/exposure-eclipse/sync-bound/prog-farmers-us-27")
    assert r_sync.status_code == 200

    # Phase 7: Technical Pricing, Seasonal Earning, FX Conversion, Model Change
    r_tp = client.post(
        "/api/pricing/technical",
        json={"expectedLossM": 6.2, "allocatedCapitalM": 42.0, "limitM": 50.0, "quotedRolPct": 22.0},
    )
    assert r_tp.status_code == 200
    assert r_tp.json()["technicalRolPct"] > 0.0

    r_seas = client.get("/api/periods/seasonal?regionPeril=US-FL-WS&asOfDay=270")
    assert r_seas.status_code == 200
    assert len(r_seas.json()["monthlyRiskPatternPct"]) == 12

    r_fx = client.post("/api/fx/convert", json={"amount": 100.0, "fromCurrency": "EUR", "toCurrency": "USD"})
    assert r_fx.status_code == 200
    assert r_fx.json()["amount"] == 109.0

    r_mc = client.post(
        "/api/model-change/compare",
        json={"programmeId": "prog-farmers-us-27", "oldSimSetId": "sim-synth-cat-v1-100k", "newSimSetId": rms_sim_id},
    )
    assert r_mc.status_code == 200
    assert len(r_mc.json()["layerMovements"]) >= 1

    # Phases 8 & 9: Fund Ops, Slip Intake & Alerts
    assert len(client.get("/api/fund-ops/investors").json()) >= 2
    assert len(client.get("/api/fund-ops/reserves").json()) >= 1
    assert len(client.get("/api/fund-ops/cash").json()) >= 1
    assert len(client.get("/api/market-marks").json()) >= 2

    r_slip = client.post(
        "/api/intake/parse-slip",
        json={"rawText": "Cedent: Beacon Property Mutual. Layer: 30m xs 85m Florida Windstorm Cat XL, 21.5% ROL, 72 hours clause."},
    )
    assert r_slip.status_code == 200
    assert r_slip.json()["limitM"] == 30.0
    assert r_slip.json()["attachmentM"] == 85.0
    assert r_slip.json()["requiresHumanConfirmation"] is True
