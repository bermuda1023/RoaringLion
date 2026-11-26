"""A Pricing Output binds one layer: a second connection is refused, and a reconnect moves the bind."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.domain import GraphEdgeSpec, GraphNodeSpec, GraphSpec
from app.models.enums import NodeType
from app.services.pricing_bind import LAYER_ALREADY_BOUND, connect_nodes

client = TestClient(app)


def _create(name: str) -> dict:
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
            "limit": 40.0,
            "attachment": 60.0,
            "depositPremium": 8.0,
            "writtenLine": 1.0,
            "signedLine": 1.0,
            "brokeragePct": 0.1,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _put(pid: str, graph: dict | GraphSpec):
    body = graph.model_dump(by_alias=True, mode="json") if isinstance(graph, GraphSpec) else graph
    return client.put(f"/api/programmes/{pid}/graph", json=body)


def test_pricing_output_refuses_a_second_layer_then_moves_when_reconnected():
    c = _create("MoveBind")
    pid = c["programmeId"]
    graph = c["graph"]
    layer_id = next(n["nodeId"] for n in graph["nodes"] if n["nodeType"] == "OCC_XL")
    px_id = f"px-{layer_id}"
    flt_id = next(n["nodeId"] for n in graph["nodes"] if n["nodeType"] == "REGION_PERIL_FILTER")
    built_id = "lyr-built"

    graph["nodes"].append({
        "nodeId": built_id,
        "nodeType": "OCC_XL",
        "label": "Built Layer",
        "params": {
            "layerCode": "L2",
            "labelName": "Built Layer",
            "occLimit": 22.0,
            "occAttachment": 10.0,
            "occFranchise": 0.0,
            "reinstatements": 1.0,
            "ripRate": 1.0,
            "depositPremium": 4.0,
            "placedShare": 1.0,
        },
        "position": {"x": 600, "y": 200},
    })
    graph["edges"].append({"edgeId": "e-in-built", "source": flt_id, "target": built_id, "sign": 1, "weight": 1.0})
    saved = _put(pid, graph)
    assert saved.status_code == 200, saved.text
    saved_graph = saved.json()["graph"]

    doubled = {
        **saved_graph,
        "edges": [
            *saved_graph["edges"],
            {"edgeId": "e-second", "source": built_id, "target": px_id, "sign": 1, "weight": 1.0},
        ],
    }
    refused = _put(pid, doubled)
    assert refused.status_code == 422
    assert "already linked" in refused.json()["detail"]
    assert "One pricing output binds one layer" in refused.json()["detail"]

    current = GraphSpec.model_validate(saved_graph)
    with pytest.raises(ValueError, match="already linked"):
        connect_nodes(current, pid, built_id, px_id, "e-second")

    disconnected = current.model_copy(deep=True)
    disconnected.edges = [e for e in disconnected.edges if e.target != px_id]
    moved = connect_nodes(disconnected, pid, built_id, px_id, "e-moved")
    pricing = next(n for n in moved.nodes if n.node_id == px_id)
    assert pricing.params["layerNodeId"] == built_id
    assert pricing.params["contractId"] == pid
    assert not any(e.target == px_id and e.source == layer_id for e in moved.edges)
    assert any(e.source == built_id and e.target == px_id for e in moved.edges)

    stored = _put(pid, moved)
    assert stored.status_code == 200, stored.text
    stored_px = next(n for n in stored.json()["graph"]["nodes"] if n["nodeId"] == px_id)
    assert stored_px["params"]["layerNodeId"] == built_id

    layers = client.get(f"/api/contracts/{pid}/layers").json()
    old = next(layer for layer in layers if layer["nodeId"] == layer_id)
    built = next(layer for layer in layers if layer["nodeId"] == built_id)
    assert old["linkedPricingNodeId"] is None
    assert built["linkedPricingNodeId"] == px_id
    assert built["linkedFeederNodeId"] == built_id

    dossier = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]
    assert dossier["limit"] == 22.0
    assert dossier["depositPremium"] == 4.0

    rollup = client.post(
        "/api/portfolio/rollup/slice",
        json={"portfolioName": "Moved Bind", "programmeIds": [pid], "view": "ADJUSTED", "totalCapital": 100.0},
    )
    assert rollup.status_code == 200, rollup.text
    assert [row["layerId"] for row in rollup.json()["layerAllocations"]] == [built_id]


def test_reconnect_refuses_a_layer_that_already_has_a_pricing_output():
    c = _create("Taken")
    pid = c["programmeId"]
    graph = GraphSpec.model_validate(c["graph"])
    layer_id = next(n.node_id for n in graph.nodes if n.node_type == NodeType.OCC_XL)
    px_id = f"px-{layer_id}"
    graph.nodes.append(GraphNodeSpec(
        node_id="lyr-taken",
        node_type=NodeType.OCC_XL,
        label="Taken",
        params={"layerCode": "L2", "labelName": "Taken", "occLimit": 10, "occAttachment": 10, "depositPremium": 2, "placedShare": 1},
        position={"x": 600, "y": 200},
    ))
    graph.nodes.append(GraphNodeSpec(
        node_id="px-taken",
        node_type=NodeType.PRICING_OUTPUT,
        label="Pricing · Taken",
        params={"contractId": pid, "layerNodeId": "lyr-taken", "linkedLabel": "Taken"},
        position={"x": 920, "y": 200},
    ))
    graph.edges.append(GraphEdgeSpec(edge_id="e-taken", source="lyr-taken", target="px-taken", sign=1, weight=1.0))
    disconnected = graph.model_copy(deep=True)
    disconnected.edges = [e for e in disconnected.edges if e.target != px_id]
    with pytest.raises(ValueError, match="already has a Pricing Output"):
        connect_nodes(disconnected, pid, "lyr-taken", px_id, "e-steal")
    assert LAYER_ALREADY_BOUND.startswith("That layer already has a Pricing Output")
