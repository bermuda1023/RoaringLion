"""Phase 1 Verification Suite: Persistent CMS, Parquet YELT/ELT ingestion, Background ILW Pricer, Staleness."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.engine.elt_simulator import simulate_elt_to_yelt
from app.main import app
from app.models.phase1 import EltRowInput


def test_elt_to_yelt_preserves_theoretical_aal():
    """ELT-to-YELT Monte Carlo simulator preserves theoretical AAL = sum(rate * mean_loss) within tolerance."""
    rows = [
        EltRowInput(event_id=101, annual_rate=0.20, mean_loss=40.0, sd_indep=10.0, sd_corr=5.0, region_peril_key="US-FL-WS"),
        EltRowInput(event_id=102, annual_rate=0.10, mean_loss=90.0, sd_indep=20.0, sd_corr=10.0, region_peril_key="US-TX-WS"),
        EltRowInput(event_id=103, annual_rate=0.05, mean_loss=150.0, sd_indep=30.0, sd_corr=15.0, region_peril_key="US-CA-EQ"),
    ]
    table, theoretical_aal = simulate_elt_to_yelt(
        sim_set_id="sim-test-elt",
        name="Test ELT",
        event_catalog_id="TEST_CAT",
        rows=rows,
        num_trials=100_000,
        seed=12345,
    )
    sim_aal = float(table.losses.sum()) / table.num_trials
    rel_err = abs(sim_aal - theoretical_aal) / theoretical_aal
    assert rel_err < 0.02, f"Simulated AAL {sim_aal:.4f} vs theoretical {theoretical_aal:.4f}"


def test_yelt_csv_upload_and_column_mapper():
    """Ingest a user-supplied CSV YELT with custom column names into canonical Parquet."""
    client = TestClient(app)
    csv_body = (
        "sim_year,evt,day,zone_peril,usd_loss\n"
        "1,1001,240,US-FL-WS,45.5\n"
        "2,1002,265,US-TX-WS,82.0\n"
        "5,1003,180,US-CA-EQ,120.0\n"
    )
    resp = client.post(
        "/api/sim-sets/ingest/yelt",
        files={"file": ("custom_yelt.csv", csv_body.encode("utf-8"), "text/csv")},
        data={
            "name": "Custom Broker YELT Upload",
            "event_catalog_id": "SYNTH_CAT_V1",
            "num_trials": "100",
            "currency": "USD",
            "trial_col": "sim_year",
            "event_col": "evt",
            "loss_col": "usd_loss",
            "day_col": "day",
            "region_peril_col": "zone_peril",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["totalEvents"] == 3
    assert body["expectedAnnualLoss"] == pytest.approx((45.5 + 82.0 + 120.0) / 100.0, abs=1e-4)
    assert body["parquetPath"].endswith(".parquet")


def test_background_ilw_pricing_and_hedge_effectiveness():
    """Instant ILW pricing against always-warm Industry YELT computes positive correlation and TVaR reduction."""
    client = TestClient(app)
    resp = client.post(
        "/api/ilw/price",
        json={
            "name": "US FL Wind $40B Binary Retro ILW",
            "industrySimSetId": "ind-synth-cat-v1-100k",
            "regionPerils": ["US-FL-WS"],
            "triggerType": "BINARY",
            "triggerBn": 40.0,
            "limitM": 25.0,
            "rol": 0.20,
            "direction": "EXTERNAL_RETRO",
            "portfolioView": "ADJUSTED",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["attachProbPct"] > 0.0
    assert body["expectedLossM"] > 0.0
    assert body["indemnityIndexCorrelation"] > 0.25
    assert body["tvar250ReductionM"] > 0.0
    assert body["netPortfolioTvar250M"] < body["grossPortfolioTvar250M"]
    assert body["manifest"]["manifestId"].startswith("man-ilw_price-")


def test_cms_crud_sliced_rollup_and_staleness():
    """Create a CMS contract, roll up a slice, mutate uplifts, and verify staleness detection flags old manifest."""
    client = TestClient(app)

    # 1. Evaluate Farmers before uplift mutation
    r_eval = client.post("/api/programmes/prog-farmers-us-27/evaluate")
    assert r_eval.status_code == 200
    old_manifest_id = r_eval.json()["manifest"]["manifestId"]

    # 2. Mutate Farmers uplift -> old manifest must become STALE
    r_up = client.put(
        "/api/programmes/prog-farmers-us-27/uplifts",
        json=[{"regionPerilKey": "US-FL-WS", "flatSeverityFactor": 1.25, "curvePoints": [], "additiveLoad": 0.0}],
    )
    assert r_up.status_code == 200

    r_stale = client.get("/api/lineage/staleness")
    assert r_stale.status_code == 200
    stale_map = {s["manifestId"]: s for s in r_stale.json()}
    assert stale_map[old_manifest_id]["isStale"] is True
    assert len(stale_map[old_manifest_id]["staleReasons"]) >= 1

    # 3. Create new contract in CMS & verify sliced rollup
    r_create = client.post(
        "/api/contracts",
        json={
            "cedentName": "Sunshine Coastal Ins",
            "programmeName": "FL Top & Drop XL",
            "treatyYear": 2027,
            "renewalPeriod": "MY27",
            "office": "BDA",
            "underwriter": "J. Anfosso",
            "broker": "Guy Carpenter",
            "status": "QUOTED",
            "direction": "ASSUMED",
            "structure": "OCC_XL",
            "limit": 20.0,
            "attachment": 90.0,
            "depositPremium": 3.8,
            "rol": 0.19,
            "collateralRequired": 20.0,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
        },
    )
    assert r_create.status_code == 200
    created_id = r_create.json()["programmeId"]

    r_slice = client.post(
        "/api/portfolio/rollup/slice",
        json={
            "portfolioName": "BDA Office Slice (Bound + Quoted)",
            "view": "ADJUSTED",
            "totalCapital": 180.0,
            "offices": ["BDA"],
            "statuses": ["BOUND", "QUOTED"],
        },
    )
    assert r_slice.status_code == 200
    assert any(row["programmeId"] == created_id for row in r_slice.json()["layerAllocations"])
