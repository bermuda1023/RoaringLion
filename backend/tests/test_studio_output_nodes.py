"""Pricing Output contract links and YELT Output extraction, export and save."""

from __future__ import annotations

import io

import pyarrow.parquet as pq
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _create(name: str, limit: float, attachment: float, premium: float, signed: float) -> dict:
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": f"{name} Re",
            "programmeName": f"{name} XL",
            "treatyYear": 2027,
            "renewalPeriod": "J27",
            "office": "BDA",
            "underwriter": "Lead Underwriter",
            "broker": "Broker A",
            "status": "QUOTED",
            "direction": "ASSUMED",
            "structure": "OCC_XL",
            "limit": limit,
            "attachment": attachment,
            "depositPremium": premium,
            "rol": premium / limit,
            "writtenLine": signed,
            "signedLine": signed,
            "brokeragePct": 0.1,
            "collateralRequired": limit,
            "regionPerils": ["US-FL-WS", "US-TX-WS"],
            "perils": ["WS"],
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _adj(pid: str) -> dict[str, dict]:
    res = client.post(f"/api/programmes/{pid}/evaluate")
    assert res.status_code == 200, res.text
    return {m["nodeId"]: m for m in res.json()["views"]["ADJUSTED"]}


def test_pricing_output_takes_upstream_layer_economics():
    metrics = _adj("prog-farmers-us-27")
    pricing = metrics["out-pricing-occ"]["pricing"]
    layer = metrics["lyr-occ-1"]
    assert pricing["source"] == "UPSTREAM"
    assert pricing["layerNodeId"] == "lyr-occ-1"
    assert pricing["premium"] == 11.5
    assert abs(pricing["expectedLoss"] - layer["el"]) < 1e-3
    assert abs(pricing["brokerage"] - 11.5 * pricing["brokeragePct"]) < 1e-9
    income = pricing["ourPremium"] + pricing["expectedRip"]
    assert abs(pricing["lossRatio"] - pricing["expectedLoss"] / income) < 1e-5
    assert abs(pricing["expectedProfit"] - (income - pricing["brokerage"] - pricing["expectedLoss"])) < 1e-5
    assert metrics["out-pricing-occ"]["elr"] == round(pricing["lossRatio"], 4)
    assert metrics["out-yelt-agg"]["pricing"] is None


def test_pricing_output_links_another_contract():
    modelled = _create("Modelled", 40.0, 60.0, 8.0, 1.0)
    priced = _create("Priced", 50.0, 60.0, 10.0, 0.5)
    pid = modelled["programmeId"]
    graph = modelled["graph"]
    layer_id = next(n["nodeId"] for n in graph["nodes"] if n["nodeType"] == "OCC_XL")
    priced_layer = next(n["nodeId"] for n in priced["graph"]["nodes"] if n["nodeType"] == "OCC_XL")
    graph["nodes"].append({
        "nodeId": "price-1",
        "nodeType": "PRICING_OUTPUT",
        "label": "Pricing Output",
        "params": {"contractId": priced["programmeId"], "layerNodeId": priced_layer, "linkedLabel": "Priced · Layer"},
        "position": {"x": 900, "y": 260},
    })
    graph["edges"].append({"edgeId": "e-price", "source": layer_id, "target": "price-1", "sign": 1, "weight": 1.0})
    assert client.put(f"/api/programmes/{pid}/graph", json=graph).status_code == 200

    metrics = _adj(pid)
    pricing = metrics["price-1"]["pricing"]
    assert pricing["source"] == "LINKED"
    assert pricing["contractId"] == priced["programmeId"]
    assert pricing["signedLine"] == 0.5
    assert pricing["ourPremium"] == 5.0
    assert abs(pricing["expectedLoss"] - metrics[layer_id]["el"] * 0.5) < 1e-3
    assert any("modelled layer is 40m xs 60m" in w for w in pricing["warnings"])
    saved = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]["graph"]
    price_node = next(n for n in saved["nodes"] if n["nodeId"] == "price-1")
    assert price_node["label"] == "Pricing Output — Priced · Layer"


def test_yelt_output_summary_export_and_save():
    c = _create("Yelt", 40.0, 30.0, 8.0, 1.0)
    pid = c["programmeId"]
    out_id = next(n["nodeId"] for n in c["graph"]["nodes"] if n["nodeType"] == "YELT_OUTPUT")
    node_el = _adj(pid)[out_id]["el"]

    summary = client.get(f"/api/programmes/{pid}/nodes/{out_id}/yelt").json()
    assert summary["view"] == "ADJUSTED"
    assert summary["eventRows"] > 0
    assert abs(summary["aal"] - node_el) < 1e-3
    assert summary["maxEvent"] <= 40.0 + 1e-9
    assert summary["inputs"][0]["nodeId"].startswith("lyr-")

    csv = client.get(f"/api/programmes/{pid}/nodes/{out_id}/yelt/export?format=csv")
    assert csv.status_code == 200
    assert csv.text.splitlines()[0] == '"trial_id","event_id","day_of_year","region_peril","loss_amount"'
    assert len(csv.text.splitlines()) == summary["eventRows"] + 1
    parquet = client.get(f"/api/programmes/{pid}/nodes/{out_id}/yelt/export?format=parquet&view=UNADJUSTED")
    table = pq.read_table(io.BytesIO(parquet.content))
    assert table.column_names == ["trial_id", "event_id", "day_of_year", "region_peril", "loss_amount"]

    saved = client.post(f"/api/programmes/{pid}/nodes/{out_id}/yelt/save", json={"name": "Yelt layer ceded"})
    assert saved.status_code == 200, saved.text
    detail = saved.json()
    assert detail["sourceFormat"] == "DERIVED"
    assert detail["totalEvents"] == summary["eventRows"]
    assert detail["manifest"]["runType"] == "SIM_SET_FROM_NODE"
    listed = {s["simSetId"]: s for s in client.get("/api/sim-sets").json()}
    assert listed[detail["simSetId"]]["name"] == "Yelt layer ceded"

    assert client.get(f"/api/programmes/{pid}/nodes/nope/yelt").status_code == 404
