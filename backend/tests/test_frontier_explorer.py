"""Verification tests for the Layer-Level Optimization Frontier Explorer, Exports, and CMS Profile Dossier."""

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


def test_portfolio_and_programme_exports_include_manifest_provenance():
    r_csv = client.get("/api/exports/portfolio-csv?view=ADJUSTED&totalCapital=160")
    assert r_csv.status_code == 200
    assert "text/csv" in r_csv.headers["content-type"]
    assert "Manifest ID" in r_csv.text
    assert "Euler Co-TVaR Layer Allocation" in r_csv.text

    r_pdf = client.get("/api/exports/portfolio-tearsheet?view=ADJUSTED&totalCapital=160")
    assert r_pdf.status_code == 200
    assert "ROARING LION — Portfolio Tear-Sheet" in r_pdf.text
    assert "Breadcrumb Provenance" in r_pdf.text

    r_deal = client.get("/api/exports/programme-tearsheet/prog-farmers-us-27")
    assert r_deal.status_code == 200
    assert "Underwriting Deal Tear-Sheet" in r_deal.text
    assert "Farmers Group" in r_deal.text


def test_cms_profile_notes_and_360_dossier():
    # Update programme profile metadata (unique code + RARC + credit rating + broker contact)
    r_upd = client.put(
        "/api/contracts/prog-farmers-us-27",
        json={
            "profile": {
                "uniqueCode": "RL-2027-001",
                "cedentCreditRating": "AM Best A+",
                "brokerContact": "Treaty Desk Lead",
                "rarcTotalPct": 7.25,
                "economicInflationPct": 3.5,
            }
        },
    )
    assert r_upd.status_code == 200
    assert r_upd.json()["profile"]["rarcTotalPct"] == 7.25

    # Post an underwriting note and verify retrieval
    r_note = client.post(
        "/api/contracts/prog-farmers-us-27/notes",
        json={"author": "Lead Underwriter", "category": "PRICING", "body": "Confirmed +10% FL uplift and underlying QS inuring."},
    )
    assert r_note.status_code == 200
    assert r_note.json()["category"] == "PRICING"

    # Fetch full 360-degree dossier linking pricing, ExposureEclipse, modeling, and notes
    r_dos = client.get("/api/contracts/prog-farmers-us-27/dossier")
    assert r_dos.status_code == 200
    dos = r_dos.json()
    assert dos["contract"]["profile"]["cedentCreditRating"] == "AM Best A+"
    assert any("underlying QS inuring" in n["body"] for n in dos["notes"])
    assert len(dos["pricingLink"]["adjustedNodes"]) >= 4
    assert dos["exposureLink"] is not None
    assert dos["exposureLink"]["edmDatabaseName"] == "EDM_FARMERS_2027_Q1"
    assert dos["modelingLink"]["simSet"] is not None


def test_yoy_programme_renewal_chain_and_cycle_guard():
    # Farmers 2027 is seeded linked to Farmers 2026 (EXPIRED)
    r_chain = client.get("/api/contracts/prog-farmers-us-27/chain")
    assert r_chain.status_code == 200
    chain = r_chain.json()
    assert [row["treatyYear"] for row in chain] == [2027, 2026]
    assert chain[0]["yoyLimitDeltaM"] == 5.0  # 85m - 80m

    # Self-link and cycle link must be rejected with 422
    assert client.post("/api/contracts/prog-farmers-us-27/link-prior", json={"priorContractId": "prog-farmers-us-27"}).status_code == 422
    assert client.post("/api/contracts/prog-farmers-us-26/link-prior", json={"priorContractId": "prog-farmers-us-27"}).status_code == 422

    # One-click Renew into 2028 clones DAG + uplifts and extends the chain to 3 years (2028 -> 2027 -> 2026)
    r_ren = client.post(
        "/api/contracts/prog-farmers-us-27/renew",
        json={"treatyYear": 2028, "limit": 90.0, "rol": 0.24, "rarcTotalPct": 5.5},
    )
    assert r_ren.status_code == 200
    new_28 = r_ren.json()
    assert new_28["treatyYear"] == 2028
    assert new_28["priorContractId"] == "prog-farmers-us-27"
    assert new_28["profile"]["priorYearLimitM"] == 85.0

    r_chain3 = client.get(f"/api/contracts/{new_28['programmeId']}/chain")
    assert r_chain3.status_code == 200
    assert [row["treatyYear"] for row in r_chain3.json()] == [2028, 2027, 2026]
