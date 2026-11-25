"""Contract layer editing: written/signed lines and terms flow into the programme graph and contract totals."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _new_contract() -> dict:
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": "Layer Test Mutual",
            "programmeName": "Layer Test Cat XL",
            "treatyYear": 2027,
            "renewalPeriod": "J27",
            "office": "BDA",
            "underwriter": "Lead Underwriter",
            "broker": "Broker A",
            "status": "QUOTED",
            "direction": "ASSUMED",
            "structure": "OCC_XL",
            "limit": 40.0,
            "attachment": 60.0,
            "depositPremium": 8.0,
            "rol": 0.2,
            "collateralRequired": 40.0,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_layer_lines_and_terms_round_trip():
    contract = _new_contract()
    pid = contract["programmeId"]

    layers = client.get(f"/api/contracts/{pid}/layers").json()
    assert len(layers) == 1
    layer = layers[0]
    assert layer["nodeType"] == "OCC_XL"
    assert {"limit", "attachment", "writtenLine", "signedLine"} <= set(layer["editable"])

    res = client.put(
        f"/api/contracts/{pid}/layers",
        json={
            "layers": [
                {
                    "nodeId": layer["nodeId"],
                    "limit": 50.0,
                    "attachment": 75.0,
                    "depositPremium": 10.0,
                    "writtenLine": 0.25,
                    "signedLine": 0.2,
                }
            ]
        },
    )
    assert res.status_code == 200, res.text
    updated = res.json()[0]
    assert updated["limit"] == 50.0
    assert updated["attachment"] == 75.0
    assert updated["writtenLine"] == 0.25
    assert updated["signedLine"] == 0.2
    assert abs(updated["ourLimit"] - 10.0) < 1e-9
    assert abs(updated["ourPremium"] - 2.0) < 1e-9
    assert abs(updated["rol"] - 0.2) < 1e-9

    full = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]
    assert full["limit"] == 50.0
    assert full["depositPremium"] == 10.0
    assert full["writtenLine"] == 0.25
    assert full["signedLine"] == 0.2
    assert full["attachment"] == 75.0
    node = next(n for n in full["graph"]["nodes"] if n["nodeId"] == layer["nodeId"])
    assert node["params"]["placedShare"] == 0.2
    assert node["params"]["occLimit"] == 50.0
    assert full["graph"]["version"] == contract["graph"]["version"] + 1


def test_layer_update_rejects_invalid_lines():
    pid = _new_contract()["programmeId"]
    node_id = client.get(f"/api/contracts/{pid}/layers").json()[0]["nodeId"]
    res = client.put(f"/api/contracts/{pid}/layers", json={"layers": [{"nodeId": node_id, "signedLine": 1.4}]})
    assert res.status_code == 422
    res = client.put(f"/api/contracts/{pid}/layers", json={"layers": [{"nodeId": "nope", "signedLine": 0.5}]})
    assert res.status_code == 404
