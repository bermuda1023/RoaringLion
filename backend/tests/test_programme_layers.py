"""Programmes with many layers: create with a tower, add and remove layers, totals roll up."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _create(name: str, layers: list[dict] | None = None) -> dict:
    payload = {
        "cedentName": f"{name} Re",
        "programmeName": f"{name} Tower",
        "status": "QUOTED",
        "direction": "ASSUMED",
        "limit": 40.0,
        "attachment": 60.0,
        "depositPremium": 8.0,
        "regionPerils": ["US-FL-WS", "US-TX-WS"],
        "perils": ["WS"],
    }
    if layers is not None:
        payload["layers"] = layers
    res = client.post("/api/contracts", json=payload)
    assert res.status_code == 200, res.text
    return res.json()


def _adj(pid: str) -> dict[str, dict]:
    res = client.post(f"/api/programmes/{pid}/evaluate")
    assert res.status_code == 200, res.text
    return {m["nodeId"]: m for m in res.json()["views"]["ADJUSTED"]}


def test_create_programme_with_many_layers():
    c = _create(
        "Many",
        [
            {"limit": 25, "attachment": 25, "depositPremium": 7.5, "signedLine": 0.5, "writtenLine": 0.6},
            {"name": "Middle", "limit": 50, "attachment": 50, "depositPremium": 8.0, "signedLine": 0.25},
            {"layerType": "AGG_XL", "limit": 30, "attachment": 60, "depositPremium": 3.0, "perEventCap": 20},
        ],
    )
    pid = c["programmeId"]
    layers = client.get(f"/api/contracts/{pid}/layers").json()
    assert [l["name"] for l in layers] == ["1st Cat XL", "Middle", "3rd Agg XL"]
    assert [l["nodeType"] for l in layers] == ["OCC_XL", "OCC_XL", "AGG_XL"]
    assert layers[0]["label"].startswith("1st Cat XL — 25m xs 25m")
    assert layers[2]["terms"] == "30m xs 60m · 20m event cap"

    assert c["limit"] == 105
    assert c["depositPremium"] == 18.5
    assert c["attachment"] == 25
    assert abs(c["signedLine"] - (25 * 0.5 + 50 * 0.25 + 30 * 1.0) / 105) < 1e-6
    assert abs(c["writtenLine"] - (25 * 0.6 + 50 * 0.25 + 30 * 1.0) / 105) < 1e-6

    nodes = {n["nodeId"]: n for n in c["graph"]["nodes"]}
    pricing = [n for n in nodes.values() if n["nodeType"] == "PRICING_OUTPUT"]
    assert len(pricing) == 3
    assert sorted(n["label"] for n in pricing) == ["Pricing · 1st Cat XL", "Pricing · 3rd Agg XL", "Pricing · Middle"]
    yelt_inputs = {e["source"] for e in c["graph"]["edges"] if e["target"] == f"out-{pid}"}
    assert yelt_inputs == {l["nodeId"] for l in layers}

    metrics = _adj(pid)
    for layer in layers:
        priced = metrics[f"px-{layer['nodeId']}"]["pricing"]
        assert priced["layerNodeId"] == layer["nodeId"]
        assert abs(priced["expectedLoss"] - metrics[layer["nodeId"]]["el"]) < 1e-3
    total_el = sum(metrics[l["nodeId"]]["el"] for l in layers)
    assert abs(metrics[f"out-{pid}"]["el"] - total_el) < 1e-3


def test_single_layer_programme_still_defaults_from_limit():
    c = _create("Single")
    layers = client.get(f"/api/contracts/{c['programmeId']}/layers").json()
    assert len(layers) == 1
    assert layers[0]["limit"] == 40 and layers[0]["attachment"] == 60
    assert layers[0]["name"] == "Single Tower"


def test_add_and_remove_layers():
    c = _create("Grow")
    pid = c["programmeId"]
    first = client.get(f"/api/contracts/{pid}/layers").json()[0]

    added = client.post(
        f"/api/contracts/{pid}/layers",
        json={"name": "Top", "limit": 60, "attachment": 100, "depositPremium": 6.0, "signedLine": 0.5},
    )
    assert added.status_code == 200, added.text
    layers = added.json()
    assert [l["name"] for l in layers] == ["Grow Tower", "Top"]
    top = layers[1]
    assert top["writtenLine"] == 0.5 and top["signedLine"] == 0.5

    contract = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]
    assert contract["limit"] == 100
    assert contract["depositPremium"] == 14.0
    graph = contract["graph"]
    inputs = {e["source"] for e in graph["edges"] if e["target"] == top["nodeId"]}
    assert inputs == {f"flt-{pid}"}
    assert {e["source"] for e in graph["edges"] if e["target"] == f"out-{pid}"} == {first["nodeId"], top["nodeId"]}
    assert _adj(pid)[f"px-{top['nodeId']}"]["pricing"]["ourPremium"] == 3.0

    third = client.post(f"/api/contracts/{pid}/layers", json={"layerType": "AGG_XL", "limit": 20, "attachment": 40}).json()[2]
    assert third["name"] == "3rd Agg XL"

    removed = client.delete(f"/api/contracts/{pid}/layers/{top['nodeId']}")
    assert removed.status_code == 200, removed.text
    assert [l["nodeId"] for l in removed.json()] == [first["nodeId"], third["nodeId"]]
    graph = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]["graph"]
    ids = {n["nodeId"] for n in graph["nodes"]}
    assert f"px-{top['nodeId']}" not in ids
    assert f"out-{pid}" in ids

    client.delete(f"/api/contracts/{pid}/layers/{third['nodeId']}")
    last = client.delete(f"/api/contracts/{pid}/layers/{first['nodeId']}")
    assert last.status_code == 422
    assert "at least one layer" in last.json()["detail"]
    assert client.delete(f"/api/contracts/{pid}/layers/nope").status_code == 404
    bad = client.post(f"/api/contracts/{pid}/layers", json={"limit": 0, "attachment": 10})
    assert bad.status_code == 422


def test_each_layer_has_its_own_price_lines_and_brokerage():
    c = _create(
        "Priced",
        [
            {"limit": 20, "attachment": 30, "rol": 0.25, "authorizedLine": 0.5, "writtenLine": 0.4,
             "signedLine": 0.3, "brokeragePct": 0.15, "technicalRol": 0.2},
            {"limit": 40, "attachment": 50, "rol": 0.10, "signedLine": 0.1, "brokeragePct": 0.05},
        ],
    )
    pid = c["programmeId"]
    low, high = client.get(f"/api/contracts/{pid}/layers").json()
    assert low["depositPremium"] == 5.0 and abs(low["rol"] - 0.25) < 1e-9
    assert (low["authorizedLine"], low["writtenLine"], low["signedLine"]) == (0.5, 0.4, 0.3)
    assert low["brokeragePct"] == 0.15 and low["technicalRol"] == 0.2
    assert abs(low["priceAdequacy"] - 1.25) < 1e-6
    assert abs(low["ourNetPremium"] - 5.0 * 0.3 * 0.85) < 1e-6
    assert high["depositPremium"] == 4.0 and high["technicalRol"] is None and high["priceAdequacy"] is None
    assert "rol" in low["editable"] and "brokeragePct" in low["editable"]

    assert c["depositPremium"] == 9.0
    assert abs(c["brokeragePct"] - (5.0 * 0.15 + 4.0 * 0.05) / 9.0) < 1e-6
    assert abs(c["authorizedLine"] - (20 * 0.5 + 40 * 1.0) / 60) < 1e-6

    priced = _adj(pid)[f"px-{low['nodeId']}"]["pricing"]
    assert priced["brokeragePct"] == 0.15 and priced["technicalRol"] == 0.2

    res = client.put(
        f"/api/contracts/{pid}/layers",
        json={"layers": [{"nodeId": high["nodeId"], "rol": 0.125, "technicalRol": 0.1, "brokeragePct": 0.2}]},
    )
    assert res.status_code == 200, res.text
    high = res.json()[1]
    assert high["depositPremium"] == 5.0 and abs(high["priceAdequacy"] - 1.25) < 1e-6
    contract = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]
    assert contract["depositPremium"] == 10.0
    assert abs(contract["technicalRol"] - (20 * 0.2 + 40 * 0.1) / 60) < 1e-6

    bad = client.put(f"/api/contracts/{pid}/layers", json={"layers": [{"nodeId": high["nodeId"], "brokeragePct": 0.95}]})
    assert bad.status_code == 422
