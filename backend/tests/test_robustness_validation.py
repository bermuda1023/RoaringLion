"""Robustness sweep: invalid input must be a clean 4xx, never a 500 / Rust panic,
and nonsensical state must not be persisted."""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_zero_capital_is_422_not_500():
    r = client.post("/api/optimizer/solve", json={"totalCapital": 0})
    assert r.status_code == 422
    r = client.post("/api/allocation/gross-net", json={"totalCapital": 0})
    assert r.status_code == 422


def test_bad_enums_and_methods_are_422():
    assert client.post("/api/optimizer/solve", json={"mode": "BOGUS"}).status_code == 422
    assert client.post("/api/allocation/gross-net", json={"allocationMethod": "BOGUS"}).status_code == 422
    assert client.post("/api/synthetic/generate", json={"frequencyDist": "BOGUS"}).status_code == 422


def test_inverted_year_ranges_are_422():
    assert client.post("/api/backtest/matrix", json={"startYear": 2025, "endYear": 1990}).status_code == 422
    r = client.post("/api/burn-cost/evaluate", json={"programmeId": "prog-farmers-us-27", "startYear": 2025, "endYear": 1990})
    assert r.status_code == 422


def test_negative_losses_and_uplifts_are_422():
    r = client.post(
        "/api/deterministic/run",
        json={
            "programmeId": "prog-farmers-us-27",
            "losses": [{"lossId": "N", "eventName": "neg", "occurrenceDatetime": "2024-01-01T00:00:00Z", "regionPerilKey": "US-FL-WS", "groundUpLoss": -1}],
        },
    )
    assert r.status_code == 422
    r = client.put(
        "/api/programmes/prog-farmers-us-27/uplifts",
        json=[{"regionPerilKey": "US-FL-WS", "flatSeverityFactor": -2, "curvePoints": [], "additiveLoad": 0}],
    )
    assert r.status_code == 422


def test_graph_validation_rejects_bad_terms_duplicates_and_weights():
    prog = client.get("/api/programmes/prog-farmers-us-27").json()
    graph = copy.deepcopy(prog["graph"])

    bad = copy.deepcopy(graph)
    for n in bad["nodes"]:
        if n["nodeType"] == "OCC_XL":
            n["params"]["occLimit"] = -50
    assert client.put("/api/programmes/prog-farmers-us-27/graph", json=bad).status_code == 422

    dup = copy.deepcopy(graph)
    dup["nodes"] = dup["nodes"] + [dict(dup["nodes"][0])]
    assert client.put("/api/programmes/prog-farmers-us-27/graph", json=dup).status_code == 422

    heavy = copy.deepcopy(graph)
    heavy["edges"][0]["weight"] = 1e9
    assert client.put("/api/programmes/prog-farmers-us-27/graph", json=heavy).status_code == 422

    # Baseline graph untouched: evaluate still works
    assert client.post("/api/programmes/prog-farmers-us-27/evaluate").status_code == 200


def test_branch_claim_and_model_run_references_are_checked():
    assert client.post("/api/branches", json={"name": "x", "signedLineOverrides": {"nope": 0.5}}).status_code == 422
    assert client.post("/api/branches", json={"name": "x", "signedLineOverrides": {"prog-farmers-us-27": -1}}).status_code == 422
    assert client.post("/api/branches", json={"name": "x", "signedLineOverrides": {"prog-farmers-us-27": 100}}).status_code == 422

    payload = {
        "claimNo": "ROB-1", "programmeId": "nope", "eventName": "x",
        "occurrenceDatetime": "2024-01-01T00:00:00Z", "regionPerilKey": "US-FL-WS",
        "groundUpLoss": 10, "status": "OPEN",
    }
    assert client.post("/api/claims", json=payload).status_code == 404
    payload["programmeId"] = "prog-farmers-us-27"
    payload["groundUpLoss"] = -5
    assert client.post("/api/claims", json=payload).status_code == 422
    payload["groundUpLoss"] = 10
    assert client.post("/api/claims", json=payload).status_code == 200
    assert client.post("/api/claims", json=payload).status_code == 409

    assert client.post("/api/model-runs", json={"vendor": "RMS", "programmeId": "nope", "numTrials": 1000}).status_code == 404
    assert client.post("/api/model-runs", json={"vendor": "RMS", "programmeId": "prog-farmers-us-27", "numTrials": 0}).status_code == 422
    assert client.post("/api/synthetic/generate", json={"numTrials": 0}).status_code == 422


def test_contract_with_unknown_sim_set_is_422():
    prog = client.get("/api/contracts")[0]
    payload = {k: v for k, v in prog.items() if k in ("cedentName", "programmeName", "status", "simSetId")}
    payload["programmeId"] = "prog-rob-badsim"
    payload["simSetId"] = "nope"
    assert client.post("/api/contracts", json=payload).status_code == 422
    # Status-only patch still works (no simSetId in patch)
    r = client.put("/api/contracts/prog-farmers-us-27", json={"status": "BOUND"})
    assert r.status_code == 200


def test_misc_negative_inputs_are_422():
    assert client.post("/api/fx/convert", json={"amount": -100, "fromCurrency": "EUR", "toCurrency": "USD"}).status_code == 422
    assert client.post("/api/pricing/technical", json={"expectedLossM": -5, "allocatedCapitalM": 10, "limitM": 50}).status_code == 422
    assert client.post("/api/pricing/technical", json={"expectedLossM": 5, "allocatedCapitalM": 10, "limitM": 0}).status_code == 422
    assert client.get("/api/periods/seasonal?regionPeril=XX-YY&asOfDay=270").status_code == 422
    assert client.post("/api/intake/parse-slip", json={"rawText": "  "}).status_code == 422
    assert client.post("/api/optimizer/frontier?num_points=50", json={"totalCapital": 160}).status_code == 422
    assert client.post("/api/backtest/matrix", json={"branchId": "nope"}).status_code == 404
