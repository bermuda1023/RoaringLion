"""Phase 2b Tests: Hours-Clause Grouping, Historical As-If & Burn Cost, Versioned Live Claims."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.engine.burn_cost import group_by_hours_clause
from app.main import app
from app.models.domain import DeterministicLossRow


def test_hours_clause_grouping_merges_within_window():
    rows = [
        DeterministicLossRow(loss_id="1", event_name="Squall A", occurrence_datetime="2027-09-10T00:00:00Z", region_peril_key="US-FL-WS", ground_up_loss=60.0),
        DeterministicLossRow(loss_id="2", event_name="Squall B", occurrence_datetime="2027-09-12T12:00:00Z", region_peril_key="US-FL-WS", ground_up_loss=40.0),
        DeterministicLossRow(loss_id="3", event_name="Later Storm", occurrence_datetime="2027-09-25T00:00:00Z", region_peril_key="US-FL-WS", ground_up_loss=50.0),
    ]
    grouped = group_by_hours_clause(rows, hours_clause=72)
    assert len(grouped) == 2
    assert grouped[0].ground_up_loss == pytest.approx(100.0)
    assert grouped[1].ground_up_loss == pytest.approx(50.0)


def test_burn_cost_and_live_claims_versioning():
    client = TestClient(app)

    # 1. Historical As-If Burn Cost (1990-2025)
    r_bc = client.post(
        "/api/burn-cost/evaluate",
        json={
            "programmeId": "prog-farmers-us-27",
            "startYear": 1990,
            "endYear": 2025,
            "annualSeverityTrend": 0.03,
            "credibilityWeightBurn": 0.35,
        },
    )
    assert r_bc.status_code == 200
    bc = r_bc.json()
    assert bc["numYears"] == 36
    assert bc["averageAnnualBurnM"] > 0.0
    assert bc["credibilityBlendedElM"] is not None
    assert bc["recommendedTechnicalRolPct"] > 0.0

    # 2. Live Claims intake -> revision (never overwrites v1) -> chronological recovery statement
    r_c1 = client.post(
        "/api/claims",
        json={
            "claimNo": "CLM-2027-001",
            "programmeId": "prog-farmers-us-27",
            "eventName": "Hurricane Delta (FL)",
            "occurrenceDatetime": "2027-09-04T10:00:00Z",
            "regionPerilKey": "US-FL-WS",
            "groundUpLoss": 100.0,
            "status": "ESTIMATE",
            "note": "Initial PCS flash estimate",
        },
    )
    assert r_c1.status_code == 200
    cid = r_c1.json()["claimId"]
    assert r_c1.json()["currentVersion"] == 1

    r_rev = client.post(
        f"/api/claims/{cid}/revise",
        json={"groundUpLoss": 125.0, "status": "CASE_RESERVE", "note": "Cedent bordereaux reserve update"},
    )
    assert r_rev.status_code == 200
    assert r_rev.json()["currentVersion"] == 2
    assert len(r_rev.json()["versions"]) == 2  # v1 preserved!

    r_eval = client.post("/api/claims/evaluate/prog-farmers-us-27")
    assert r_eval.status_code == 200
    assert r_eval.json()["totalCeded"] > 0.0
