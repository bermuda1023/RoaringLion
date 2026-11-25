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

    # Priced contract's default Pricing Output is already linked 1:1 to priced_layer -> rejected with 422
    conflict = client.put(f"/api/programmes/{pid}/graph", json=graph)
    assert conflict.status_code == 422
    assert "1:1" in conflict.json()["detail"]

    # Unlink priced contract's default Pricing Output so the 1:1 slot is available
    priced_graph = priced["graph"]
    for n in priced_graph["nodes"]:
        if n["nodeType"] == "PRICING_OUTPUT":
            n["params"] = {"unlinked": True}
    assert client.put(f"/api/programmes/{priced['programmeId']}/graph", json=priced_graph).status_code == 200

    assert client.put(f"/api/programmes/{pid}/graph", json=graph).status_code == 200

    metrics = _adj(pid)
    pricing = metrics["price-1"]["pricing"]
    assert pricing["source"] == "LINKED"
    assert pricing["rollsUp"] is True
    assert pricing["feederNodeId"] == layer_id
    assert pricing["contractId"] == priced["programmeId"]
    assert pricing["signedLine"] == 0.5
    assert pricing["ourPremium"] == 5.0
    assert abs(pricing["expectedLoss"] - metrics[layer_id]["el"] * 0.5) < 1e-3
    assert any("modelled layer is 40m xs 60m" in w for w in pricing["warnings"])
    saved = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]["graph"]
    price_node = next(n for n in saved["nodes"] if n["nodeId"] == "price-1")
    assert price_node["label"] == "Pricing Output — Priced · Layer"


def test_pricing_output_one_to_one_within_programme():
    c = _create("OneToOne", 40.0, 60.0, 8.0, 1.0)
    pid = c["programmeId"]
    graph = c["graph"]
    layer_id = next(n["nodeId"] for n in graph["nodes"] if n["nodeType"] == "OCC_XL")

    # Adding a second Pricing Output linked to the same CMS layer is rejected (1:1)
    graph["nodes"].append({
        "nodeId": "px-dup",
        "nodeType": "PRICING_OUTPUT",
        "label": "Duplicate Link",
        "params": {"contractId": pid, "layerNodeId": layer_id},
        "position": {"x": 920, "y": 220},
    })
    graph["edges"].append({"edgeId": "e-dup", "source": layer_id, "target": "px-dup", "sign": 1, "weight": 1.0})
    dup_res = client.put(f"/api/programmes/{pid}/graph", json=graph)
    assert dup_res.status_code == 422
    assert "1:1" in dup_res.json()["detail"]

    # Unlinking the first Pricing Output allows the second to claim the 1:1 link
    for n in graph["nodes"]:
        if n["nodeId"] == f"px-{layer_id}":
            n["params"] = {"unlinked": True}
    ok_res = client.put(f"/api/programmes/{pid}/graph", json=graph)
    assert ok_res.status_code == 200, ok_res.text


def test_many_priced_layers_only_linked_pricing_outputs_roll_up():
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": "Selective Rollup Re",
            "programmeName": "Selective Tower",
            "status": "BOUND",
            "direction": "ASSUMED",
            "limit": 80.0,
            "attachment": 50.0,
            "depositPremium": 16.0,
            "regionPerils": ["US-FL-WS", "US-TX-WS"],
            "perils": ["WS"],
            "layers": [
                {"layerCode": "L1", "name": "Booked Layer 1", "limit": 50.0, "attachment": 50.0, "depositPremium": 10.0, "signedLine": 1.0},
                {"layerCode": "L2", "name": "Unlinked Layer 2", "limit": 30.0, "attachment": 100.0, "depositPremium": 6.0, "signedLine": 1.0},
            ],
        },
    )
    assert res.status_code == 200, res.text
    c = res.json()
    pid = c["programmeId"]
    graph = c["graph"]
    flt_id = next(n["nodeId"] for n in graph["nodes"] if n["nodeType"] == "REGION_PERIL_FILTER")
    l1_id = next(n["nodeId"] for n in graph["nodes"] if n["params"].get("layerCode") == "L1")
    l2_id = next(n["nodeId"] for n in graph["nodes"] if n["params"].get("layerCode") == "L2")

    # Unlink L2's Pricing Output and add an exploratory candidate layer Opt C with its own unlinked Pricing Output
    for n in graph["nodes"]:
        if n["nodeId"] == f"px-{l2_id}":
            n["params"] = {"unlinked": True}
    graph["nodes"].extend([
        {
            "nodeId": "opt-c",
            "nodeType": "OCC_XL",
            "label": "Option C",
            "params": {
                "layerCode": "OPT-C",
                "labelName": "Option C",
                "occLimit": 40.0,
                "occAttachment": 40.0,
                "occFranchise": 0.0,
                "reinstatements": 1.0,
                "ripRate": 1.0,
                "depositPremium": 9.0,
                "placedShare": 1.0,
            },
            "position": {"x": 600, "y": 360},
        },
        {
            "nodeId": "px-opt-c",
            "nodeType": "PRICING_OUTPUT",
            "label": "Pricing · Option C",
            "params": {"unlinked": True},
            "position": {"x": 920, "y": 360},
        },
    ])
    graph["edges"].extend([
        {"edgeId": "e-in-opt-c", "source": flt_id, "target": "opt-c", "sign": 1, "weight": 1.0},
        {"edgeId": "e-px-opt-c", "source": "opt-c", "target": "px-opt-c", "sign": 1, "weight": 1.0},
    ])
    save = client.put(f"/api/programmes/{pid}/graph", json=graph)
    assert save.status_code == 200, save.text

    # 1. All 3 Pricing Outputs are priced on the canvas, but only L1 is marked rollsUp=True
    metrics = _adj(pid)
    assert metrics[f"px-{l1_id}"]["pricing"]["source"] == "LINKED"
    assert metrics[f"px-{l1_id}"]["pricing"]["rollsUp"] is True
    assert metrics[f"px-{l2_id}"]["pricing"]["source"] == "UPSTREAM"
    assert metrics[f"px-{l2_id}"]["pricing"]["rollsUp"] is False
    assert metrics["px-opt-c"]["pricing"]["source"] == "UPSTREAM"
    assert metrics["px-opt-c"]["pricing"]["rollsUp"] is False
    assert abs(metrics["px-opt-c"]["pricing"]["expectedLoss"] - metrics["opt-c"]["el"]) < 1e-3

    # 2. Contract booked totals only reflect the linked CMS layer L1
    dossier = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]
    assert dossier["limit"] == 50.0
    assert dossier["depositPremium"] == 10.0

    # 3. Portfolio slice rollup ONLY includes L1 (not L2 or Option C)
    rollup = client.post(
        "/api/portfolio/rollup/slice",
        json={"portfolioName": "Selective Slice", "programmeIds": [pid], "view": "ADJUSTED", "totalCapital": 100.0},
    )
    assert rollup.status_code == 200, rollup.text
    allocs = rollup.json()["layerAllocations"]
    assert [a["layerId"] for a in allocs] == [l1_id]
    assert abs(allocs[0]["el"] - metrics[l1_id]["el"]) < 1e-3

    # 4. Optimizer layer catalog ONLY includes L1 for this programme
    opt_layers = client.get("/api/optimizer/layers?view=ADJUSTED&mode=FORWARD_PLANNING").json()
    prog_opt_keys = [l["layerKey"] for l in opt_layers if l["programmeId"] == pid]
    assert prog_opt_keys == [f"{pid}::{l1_id}"]

    # 5. Gross-net retro allocation ONLY includes L1 for this programme
    alloc_res = client.post(
        "/api/allocation/gross-net",
        json={"view": "ADJUSTED", "totalCapital": 160.0},
    )
    assert alloc_res.status_code == 200, alloc_res.text
    prog_alloc_ids = [r["layerId"] for r in alloc_res.json()["layers"] if r["programmeId"] == pid]
    assert prog_alloc_ids == [l1_id]


def test_remapped_feeder_drives_rollup_losses_with_cms_layer_terms():
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": "Feeder Swap Re",
            "programmeName": "Feeder Swap XL",
            "status": "BOUND",
            "direction": "ASSUMED",
            "limit": 50.0,
            "attachment": 80.0,
            "depositPremium": 10.0,
            "writtenLine": 0.5,
            "signedLine": 0.5,
            "regionPerils": ["US-FL-WS", "US-TX-WS"],
            "perils": ["WS"],
        },
    )
    assert res.status_code == 200, res.text
    c = res.json()
    pid = c["programmeId"]
    graph = c["graph"]
    flt_id = next(n["nodeId"] for n in graph["nodes"] if n["nodeType"] == "REGION_PERIL_FILTER")
    cms_layer_id = next(n["nodeId"] for n in graph["nodes"] if n["nodeType"] == "OCC_XL")
    px_id = f"px-{cms_layer_id}"

    # Add an alternative feeder structure (lower attachment -> higher EL, 100% share) and wire it into px_id
    graph["nodes"].append({
        "nodeId": "alt-feeder",
        "nodeType": "OCC_XL",
        "label": "Alt Feeder",
        "params": {
            "layerCode": "ALT",
            "labelName": "Alt Feeder",
            "occLimit": 50.0,
            "occAttachment": 35.0,
            "occFranchise": 0.0,
            "reinstatements": 1.0,
            "ripRate": 1.0,
            "depositPremium": 14.0,
            "placedShare": 1.0,
        },
        "position": {"x": 600, "y": 240},
    })
    graph["edges"] = [e for e in graph["edges"] if e["target"] != px_id]
    graph["edges"].extend([
        {"edgeId": "e-in-alt", "source": flt_id, "target": "alt-feeder", "sign": 1, "weight": 1.0},
        {"edgeId": "e-alt-px", "source": "alt-feeder", "target": px_id, "sign": 1, "weight": 1.0},
    ])
    assert client.put(f"/api/programmes/{pid}/graph", json=graph).status_code == 200

    # Verify CMS layers endpoint reports the 1:1 linked Pricing Output and its feeder
    cms_layers = client.get(f"/api/contracts/{pid}/layers").json()
    cms_l1 = next(l for l in cms_layers if l["nodeId"] == cms_layer_id)
    assert cms_l1["linkedPricingNodeId"] == px_id
    assert cms_l1["linkedFeederNodeId"] == "alt-feeder"

    # Verify DAG evaluation shows feeder=alt-feeder mapped to CMS layer cms_layer_id at 50% signed line
    metrics = _adj(pid)
    px_pricing = metrics[px_id]["pricing"]
    assert px_pricing["source"] == "LINKED"
    assert px_pricing["rollsUp"] is True
    assert px_pricing["feederNodeId"] == "alt-feeder"
    assert px_pricing["layerNodeId"] == cms_layer_id
    expected_el_50pct = metrics["alt-feeder"]["el"] * 0.5
    assert abs(px_pricing["expectedLoss"] - expected_el_50pct) < 1e-3

    # Verify Portfolio Slice Rollup uses alt-feeder's losses scaled to CMS layer's 50% signed line & $5m premium
    rollup = client.post(
        "/api/portfolio/rollup/slice",
        json={"portfolioName": "Feeder Swap Slice", "programmeIds": [pid], "view": "ADJUSTED", "totalCapital": 100.0},
    )
    assert rollup.status_code == 200, rollup.text
    allocs = rollup.json()["layerAllocations"]
    assert len(allocs) == 1
    assert allocs[0]["layerId"] == cms_layer_id
    assert allocs[0]["signedLine"] == 0.5
    assert allocs[0]["limit"] == 25.0
    assert allocs[0]["premium"] == 5.0
    assert abs(allocs[0]["el"] - expected_el_50pct) < 1e-3


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
