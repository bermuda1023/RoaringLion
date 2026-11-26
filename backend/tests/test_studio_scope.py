"""Structuring Studio scope: loss-file filters, bound pricing outputs, and the renewal chain."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.domain import GraphNodeSpec, GraphSpec
from app.models.enums import NodeType
from app.services.graph_templates import apply_cms_layer_suggestion
from app.services.pricing_bind import connect_nodes

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


def _put(pid: str, graph: dict):
    return client.put(f"/api/programmes/{pid}/graph", json=graph)


def test_filter_accepts_only_regions_and_perils_on_the_loss_source():
    deal = _create("FilterFiles")
    pid = deal["programmeId"]
    graph = deal["graph"]
    source = next(n for n in graph["nodes"] if n["nodeType"] == "LOSS_SOURCE")
    filt = next(n for n in graph["nodes"] if n["nodeType"] == "REGION_PERIL_FILTER")
    source["params"] = {
        "simSetId": deal["simSetId"],
        "lossFiles": [{
            "fileId": "fl",
            "simSetId": deal["simSetId"],
            "name": "Florida wind",
            "region": "US-FL",
            "peril": "WS",
            "regionPeril": "US-FL-WS",
            "vendor": "RMS",
        }],
    }
    filt["params"] = {"regionPerils": ["JP-KANTO-EQ"]}
    refused = _put(pid, graph)
    assert refused.status_code == 422
    assert "JP-KANTO-EQ" in refused.json()["detail"]
    filt["params"] = {"regionPerils": ["US-FL-WS"]}
    accepted = _put(pid, graph)
    assert accepted.status_code == 200, accepted.text


def test_blank_loss_source_still_uses_the_programme_sim_set():
    deal = _create("FallbackFiles")
    graph = deal["graph"]
    source = next(n for n in graph["nodes"] if n["nodeType"] == "LOSS_SOURCE")
    filt = next(n for n in graph["nodes"] if n["nodeType"] == "REGION_PERIL_FILTER")
    source["params"] = {}
    filt["params"] = {"regionPerils": ["US-FL-WS"]}
    accepted = _put(deal["programmeId"], graph)
    assert accepted.status_code == 200, accepted.text


def test_elt_upload_records_one_file_per_declared_region_and_peril():
    declared = client.post(
        "/api/sim-sets/ingest/elt",
        json={
            "name": "RMS Florida",
            "numTrials": 20,
            "region": "US-FL",
            "peril": "WS",
            "vendor": "RMS",
            "rows": [{"eventId": 1, "annualRate": 0.1, "meanLoss": 10.0, "regionPerilKey": "US-TX-WS"}],
        },
    )
    assert declared.status_code == 200, declared.text
    files = declared.json()["lossFiles"]
    assert len(files) == 1
    assert files[0]["region"] == "US-FL"
    assert files[0]["peril"] == "WS"
    assert files[0]["vendor"] == "RMS"

    split = client.post(
        "/api/sim-sets/ingest/elt",
        json={
            "name": "Undeclared",
            "numTrials": 20,
            "rows": [
                {"eventId": 1, "annualRate": 0.1, "meanLoss": 10.0, "regionPerilKey": "US-FL-WS"},
                {"eventId": 2, "annualRate": 0.1, "meanLoss": 8.0, "regionPerilKey": "US-TX-WS"},
            ],
        },
    )
    assert split.status_code == 200, split.text
    pairs = sorted(item["regionPeril"] for item in split.json()["lossFiles"])
    assert pairs == ["US-FL-WS", "US-TX-WS"]


def test_seeded_sim_set_exposes_loss_files_from_its_keys():
    listed = client.get("/api/sim-sets")
    assert listed.status_code == 200, listed.text
    synth = next(item for item in listed.json() if item["simSetId"] == "sim-synth-cat-v1-100k")
    assert any(item["regionPeril"] == "US-FL-WS" for item in synth["lossFiles"])


def test_related_programmes_are_only_the_renewal_chain():
    prior = _create("ChainPrior")
    current = _create("ChainOpen")
    stranger = _create("Unrelated")
    linked = client.post(
        f"/api/contracts/{current['programmeId']}/link-prior",
        json={"priorContractId": prior["programmeId"]},
    )
    assert linked.status_code == 200, linked.text
    related = client.get(f"/api/programmes/{current['programmeId']}/related")
    assert related.status_code == 200, related.text
    ids = {row["programmeId"] for row in related.json()}
    assert prior["programmeId"] in ids
    assert current["programmeId"] in ids
    assert stranger["programmeId"] not in ids
    missing = client.get("/api/programmes/missing-programme/related")
    assert missing.status_code == 404


def test_bound_pricing_output_is_not_offered_or_saved_again():
    owner = _create("BoundOwner")
    renewal = _create("BoundRenewal")
    linked = client.post(
        f"/api/contracts/{renewal['programmeId']}/link-prior",
        json={"priorContractId": owner["programmeId"]},
    )
    assert linked.status_code == 200, linked.text
    templates = client.get("/api/graph-templates", params={"programmeId": renewal["programmeId"]})
    assert templates.status_code == 200, templates.text
    prior_cards = [item for item in templates.json() if item["templateId"].startswith(f"cms-prior-{owner['programmeId']}-")]
    assert prior_cards
    assert all(item["pricingBound"] is True and item["pricingOutput"] is None for item in prior_cards)

    bindings = client.get("/api/pricing-bindings")
    assert bindings.status_code == 200, bindings.text
    owner_layer = next(n for n in owner["graph"]["nodes"] if n["nodeType"] == "OCC_XL")
    assert any(
        row["contractId"] == owner["programmeId"] and row["layerNodeId"] == owner_layer["nodeId"]
        for row in bindings.json()
    )

    stolen = {
        **renewal["graph"],
        "nodes": [
            *renewal["graph"]["nodes"],
            {
                "nodeId": "px-stolen",
                "nodeType": "PRICING_OUTPUT",
                "label": "Stolen",
                "params": {"contractId": owner["programmeId"], "layerNodeId": owner_layer["nodeId"]},
                "position": {"x": 900, "y": 80},
            },
        ],
    }
    saved = _put(renewal["programmeId"], stolen)
    assert saved.status_code == 422
    assert "already" in saved.json()["detail"].lower()

    graph = GraphSpec.model_validate(renewal["graph"])
    layer = next(n for n in graph.nodes if n.node_type == NodeType.OCC_XL)
    graph.nodes.append(GraphNodeSpec(
        node_id="px-stolen",
        node_type=NodeType.PRICING_OUTPUT,
        label="Stolen",
        params={"contractId": owner["programmeId"], "layerNodeId": owner_layer["nodeId"]},
        position={"x": 900, "y": 80},
    ))
    with pytest.raises(ValueError, match="already bound"):
        connect_nodes(
            graph,
            renewal["programmeId"],
            layer.node_id,
            "px-stolen",
            "e-again",
            claimed_elsewhere={(owner["programmeId"], owner_layer["nodeId"])},
        )

    prior_template = next(
        item for item in templates.json() if item["templateId"].startswith(f"cms-prior-{owner['programmeId']}-")
    )
    # A caller that still sends the pricing output is refused, not given an unlinked copy.
    prior_template["pricingOutput"] = {
        "nodeType": "PRICING_OUTPUT",
        "label": "Pricing",
        "offset": {"x": 320, "y": 0},
        "params": {"contractId": owner["programmeId"], "layerNodeId": owner_layer["nodeId"]},
    }
    from app.services.graph_templates import GraphTemplate

    with pytest.raises(ValueError, match="already bound"):
        apply_cms_layer_suggestion(
            GraphSpec.model_validate(renewal["graph"]),
            GraphTemplate.model_validate(prior_template),
            claimed_elsewhere={(owner["programmeId"], owner_layer["nodeId"])},
        )
