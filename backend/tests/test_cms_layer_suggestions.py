"""CMS layer suggestions always place one Pricing Output with the layer, outside the blank canvas."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.enums import NodeType
from app.models.phase1 import ContractFull
from app.models.domain import GraphNodeSpec, GraphSpec
from app.services.graph_templates import (
    BLANK_CANVAS_X,
    GraphTemplate,
    GraphTemplatePricingOutput,
    apply_cms_layer_suggestion,
    build_graph_templates,
    in_blank_canvas,
)

client = TestClient(app)


def _create(name: str, layers: list[dict] | None = None) -> dict:
    body: dict = {
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
        "writtenLine": 0.5,
        "signedLine": 0.5,
        "brokeragePct": 0.1,
        "regionPerils": ["US-FL-WS", "US-TX-WS"],
        "perils": ["WS"],
    }
    if layers is not None:
        body["layers"] = layers
    res = client.post("/api/contracts", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _templates(pid: str) -> list[dict]:
    res = client.get(f"/api/graph-templates?programmeId={pid}")
    assert res.status_code == 200, res.text
    return res.json()


def _cms_layers(tpls: list[dict], pid: str) -> list[dict]:
    return [t for t in tpls if t.get("source") == "CMS" and t["templateId"].startswith(f"cms-layer-{pid}-")]


def _ids(factory_prefix: str):
    n = {"i": 0}

    def factory(prefix: str) -> str:
        n["i"] += 1
        return f"{factory_prefix}-{prefix}-{n['i']}"

    return factory


def test_cms_layer_templates_each_include_one_pricing_output_outside_blank_palette():
    c = _create(
        "Palette",
        [
            {"layerCode": "L1", "name": "Primary", "limit": 25, "attachment": 25, "depositPremium": 6.0, "signedLine": 1.0},
            {"layerCode": "CAT-2", "name": "Second", "limit": 50, "attachment": 50, "depositPremium": 8.0, "signedLine": 0.4, "brokeragePct": 0.12},
        ],
    )
    pid = c["programmeId"]
    tpls = _templates(pid)
    layers = _cms_layers(tpls, pid)
    assert [t["layerCode"] for t in layers] == ["L1", "CAT-2"]

    blank_ids = {t["templateId"] for t in tpls if t["source"] == "BLANK"}
    assert "blank-occ-xl" in blank_ids
    assert "blank-out-pricing" in blank_ids
    assert not any(tid.startswith("cms-layer-") or tid.startswith("cms-prior-") for tid in blank_ids)
    blank_pricing = next(t for t in tpls if t["templateId"] == "blank-out-pricing")
    assert blank_pricing["pricingOutput"] is None
    assert "contractId" not in blank_pricing["params"]

    links = []
    for tpl in layers:
        assert tpl["source"] == "CMS"
        assert tpl["placement"] == "EXISTING_LAYER"
        px = tpl["pricingOutput"]
        assert px["nodeType"] == "PRICING_OUTPUT"
        assert px["offset"] == {"x": 320.0, "y": 0.0}
        assert px["params"]["contractId"] == pid
        assert px["params"]["layerNodeId"] == tpl["layerNodeId"]
        assert tpl["layerNodeId"]
        links.append((px["params"]["contractId"], px["params"]["layerNodeId"]))
        # The suggestion itself is not a blank-canvas node.
        assert tpl["templateId"] not in blank_ids
    assert len(links) == len(set(links))

    # Seeded programme: current layers and prior-year copies each carry one pricing output.
    farmers = _templates("prog-farmers-us-27")
    current = _cms_layers(farmers, "prog-farmers-us-27")
    prior = [t for t in farmers if t["templateId"].startswith("cms-prior-prog-farmers-us-26-")]
    assert len(current) >= 2 and len(prior) >= 2
    assert {t["placement"] for t in current} == {"EXISTING_LAYER"}
    assert {t["placement"] for t in prior} == {"NEW_COPY"}
    for tpl in prior:
        assert tpl["pricingOutput"]["params"]["contractId"] == "prog-farmers-us-26"
        assert tpl["pricingOutput"]["params"]["layerNodeId"] == tpl["layerNodeId"]
    all_layer_suggestions = [t for t in farmers if t.get("source") == "CMS" and t["nodeType"] in {"OCC_XL", "AGG_XL", "AGG_QUOTA_SHARE", "QUOTA_SHARE", "SURPLUS_SHARE", "CASCADING_TOWER", "ILW_OPTION"}]
    assert all(t.get("pricingOutput") and t["pricingOutput"]["nodeType"] == "PRICING_OUTPUT" for t in all_layer_suggestions)
    farmer_blank = {t["templateId"] for t in farmers if t["source"] == "BLANK"}
    assert not any(t["templateId"] in farmer_blank for t in all_layer_suggestions)


def test_suggesting_a_cms_layer_places_one_linked_pricing_output_beside_it():
    c = _create(
        "Place",
        [{"layerCode": "L1", "name": "Cat XL", "limit": 40, "attachment": 60, "depositPremium": 8.0, "signedLine": 0.5, "brokeragePct": 0.1, "rarcPct": 5.0}],
    )
    pid = c["programmeId"]
    graph = GraphSpec.model_validate(c["graph"])
    layer_id = next(n.node_id for n in graph.nodes if n.node_type == NodeType.OCC_XL)
    layer = next(n for n in graph.nodes if n.node_id == layer_id)
    px_id = f"px-{layer_id}"

    # Already paired: suggesting again does not add a second Pricing Output.
    tpl = GraphTemplate.model_validate(next(t for t in _cms_layers(_templates(pid), pid) if t["layerNodeId"] == layer_id))
    already = apply_cms_layer_suggestion(graph, tpl, id_factory=_ids("keep"))
    assert already.changed is False
    assert already.pricing_node_id == px_id
    assert len([n for n in already.graph.nodes if n.node_type == NodeType.PRICING_OUTPUT]) == 1

    # Drop the pricing node. The suggestion puts exactly one back, beside the layer, not in the blank box.
    graph.nodes = [n for n in graph.nodes if n.node_id != px_id]
    graph.edges = [e for e in graph.edges if px_id not in (e.source, e.target)]
    placed = apply_cms_layer_suggestion(graph, tpl, id_factory=_ids("add"))
    assert placed.added_node_ids == [placed.pricing_node_id]
    assert placed.layer_node_id == layer_id
    assert placed.linked is True
    px = next(n for n in placed.graph.nodes if n.node_id == placed.pricing_node_id)
    assert px.params["contractId"] == pid
    assert px.params["layerNodeId"] == layer_id
    assert px.position["x"] == layer.position["x"] + 320
    assert px.position["y"] == layer.position["y"]
    assert not in_blank_canvas(px.position)
    assert not in_blank_canvas(layer.position)
    assert any(e.source == layer_id and e.target == px.node_id for e in placed.graph.edges)
    assert len([n for n in placed.graph.nodes if n.node_type == NodeType.PRICING_OUTPUT and n.params.get("layerNodeId") == layer_id]) == 1

    again = apply_cms_layer_suggestion(placed.graph, tpl, id_factory=_ids("again"))
    assert again.changed is False
    assert len(again.graph.nodes) == len(placed.graph.nodes)

    # The slot is held elsewhere: click, drag and placement all refuse a second use.
    with pytest.raises(ValueError, match="already bound"):
        apply_cms_layer_suggestion(
            graph,
            tpl,
            claimed_elsewhere={(pid, layer_id)},
            id_factory=_ids("block"),
        )

    saved = client.put(f"/api/programmes/{pid}/graph", json=placed.graph.model_dump(by_alias=True, mode="json"))
    assert saved.status_code == 200, saved.text
    evaluated = client.post(f"/api/programmes/{pid}/evaluate")
    assert evaluated.status_code == 200, evaluated.text
    metrics = {m["nodeId"]: m for m in evaluated.json()["views"]["ADJUSTED"]}
    pricing = metrics[placed.pricing_node_id]["pricing"]
    assert pricing["source"] == "LINKED"
    assert pricing["rollsUp"] is True
    assert pricing["layerNodeId"] == layer_id
    assert pricing["layerCode"] == "L1"
    assert pricing["premium"] == 8.0
    assert pricing["signedLine"] == 0.5
    assert pricing["ourPremium"] == 4.0
    assert pricing["brokeragePct"] == 0.1
    assert abs(pricing["brokerage"] - 0.4) < 1e-9
    assert abs(pricing["rol"] - 0.2) < 1e-9


def test_removed_cms_layer_is_restored_with_its_pricing_output_outside_the_blank_box():
    c = _create("Restore", [{"name": "Working", "limit": 30, "attachment": 40, "depositPremium": 5.0}])
    pid = c["programmeId"]
    graph = GraphSpec.model_validate(c["graph"])
    layer_id = next(n.node_id for n in graph.nodes if n.node_type == NodeType.OCC_XL)
    drop = {layer_id, f"px-{layer_id}"}
    graph.nodes = [n for n in graph.nodes if n.node_id not in drop]
    graph.edges = [e for e in graph.edges if e.source not in drop and e.target not in drop]
    tpl = GraphTemplate.model_validate(next(t for t in _cms_layers(_templates(pid), pid) if t["layerNodeId"] == layer_id))
    # Pretend the saved anchor sits in the blank-canvas scatter box; the pair must leave it.
    tpl.position = {"x": (BLANK_CANVAS_X[0] + BLANK_CANVAS_X[1]) / 2, "y": 120.0}

    placed = apply_cms_layer_suggestion(graph, tpl, id_factory=_ids("restore"))
    assert placed.layer_node_id == layer_id
    assert placed.linked is True
    layer = next(n for n in placed.graph.nodes if n.node_id == layer_id)
    px = next(n for n in placed.graph.nodes if n.node_id == placed.pricing_node_id)
    assert not in_blank_canvas(layer.position)
    assert not in_blank_canvas(px.position)
    assert px.position["x"] == layer.position["x"] + 320
    assert px.position["y"] == layer.position["y"]
    assert px.params["contractId"] == pid and px.params["layerNodeId"] == layer_id
    assert len([n for n in placed.graph.nodes if n.node_type == NodeType.PRICING_OUTPUT]) == 1

    second = apply_cms_layer_suggestion(placed.graph, tpl, id_factory=_ids("restore2"))
    assert second.changed is False
    assert len([n for n in second.graph.nodes if n.node_type == NodeType.PRICING_OUTPUT]) == 1


def test_prior_year_suggestion_copies_a_layer_with_one_pricing_output():
    host = _create("Host", [{"layerCode": "L1", "limit": 20, "attachment": 20, "depositPremium": 3.0}])
    graph = GraphSpec.model_validate(host["graph"])
    prior = next(t for t in _templates("prog-farmers-us-27") if t["templateId"].startswith("cms-prior-prog-farmers-us-26-") and t["layerCode"] == "L1")
    tpl = GraphTemplate.model_validate(prior)
    placed = apply_cms_layer_suggestion(graph, tpl, id_factory=_ids("prior"))
    assert placed.changed is True
    assert placed.layer_node_id != tpl.layer_node_id
    assert placed.linked is True
    layer = next(n for n in placed.graph.nodes if n.node_id == placed.layer_node_id)
    px = next(n for n in placed.graph.nodes if n.node_id == placed.pricing_node_id)
    assert layer.params.get("suggestedFrom") == tpl.template_id
    assert px.params["contractId"] == "prog-farmers-us-26"
    assert px.params["layerNodeId"] == tpl.layer_node_id
    assert not in_blank_canvas(layer.position)
    assert not in_blank_canvas(px.position)
    assert px.position["x"] == layer.position["x"] + 320
    assert px.position["y"] == layer.position["y"]
    # The host already uses L1, so the copy takes the next code rather than colliding.
    assert str(layer.params.get("layerCode")).lower() != "l1"

    second = apply_cms_layer_suggestion(placed.graph, tpl, id_factory=_ids("prior2"))
    assert second.changed is False
    assert second.pricing_node_id == placed.pricing_node_id
    linked = [
        n for n in second.graph.nodes
        if n.node_type == NodeType.PRICING_OUTPUT and n.params.get("layerNodeId") == tpl.layer_node_id
    ]
    assert len(linked) == 1


def test_pricing_output_leaves_the_blank_box_when_the_offset_would_land_inside_it():
    graph = GraphSpec(
        graph_id="g",
        name="g",
        version=1,
        currency="USD",
        nodes=[
            GraphNodeSpec(
                node_id="lyr-1",
                node_type=NodeType.OCC_XL,
                label="Inside",
                params={"layerCode": "L1"},
                position={"x": 200.0, "y": 100.0},
            )
        ],
        edges=[],
    )
    tpl = GraphTemplate(
        template_id="cms-layer-p-lyr-1",
        category="CMS Layer · L1",
        source="CMS",
        label="Inside",
        node_type="OCC_XL",
        params={"layerCode": "L1"},
        layer_code="L1",
        layer_node_id="lyr-1",
        contract_id="p",
        placement="EXISTING_LAYER",
        pricing_output=GraphTemplatePricingOutput(
            params={"contractId": "p", "layerNodeId": "lyr-1", "labelName": "Pricing", "linkedLabel": "P · L1"},
        ),
    )
    placed = apply_cms_layer_suggestion(graph, tpl, id_factory=_ids("nudge"))
    layer = next(n for n in placed.graph.nodes if n.node_id == "lyr-1")
    px = next(n for n in placed.graph.nodes if n.node_id == placed.pricing_node_id)
    # The layer was already on the canvas, so it stays. 200+320=520 is inside the blank box, so the
    # pricing node is shifted to x 600 on the same row.
    assert layer.position == {"x": 200.0, "y": 100.0}
    assert px.position == {"x": 600.0, "y": 100.0}
    assert not in_blank_canvas(px.position)
    assert placed.linked is True
    assert len([n for n in placed.graph.nodes if n.node_type == NodeType.PRICING_OUTPUT]) == 1


def test_restoring_a_deleted_layer_reuses_its_linked_pricing_output():
    graph = GraphSpec(
        graph_id="g",
        name="g",
        version=1,
        currency="USD",
        nodes=[
            GraphNodeSpec(
                node_id="px-lyr-1",
                node_type=NodeType.PRICING_OUTPUT,
                label="Pricing",
                params={"contractId": "p", "layerNodeId": "lyr-1", "labelName": "Pricing", "linkedLabel": "P · L1"},
                position={"x": 920.0, "y": 40.0},
            )
        ],
        edges=[],
    )
    tpl = GraphTemplate(
        template_id="cms-layer-p-lyr-1",
        category="CMS Layer · L1",
        source="CMS",
        label="Cat XL",
        node_type="OCC_XL",
        params={"layerCode": "L1", "labelName": "Cat XL"},
        layer_code="L1",
        layer_node_id="lyr-1",
        contract_id="p",
        placement="EXISTING_LAYER",
        position={"x": 450.0, "y": 120.0},
        pricing_output=GraphTemplatePricingOutput(
            params={"contractId": "p", "layerNodeId": "lyr-1", "labelName": "Pricing", "linkedLabel": "P · L1"},
        ),
    )
    placed = apply_cms_layer_suggestion(graph, tpl, id_factory=_ids("reuse"))
    assert placed.added_node_ids == ["lyr-1"]
    assert placed.pricing_node_id == "px-lyr-1"
    assert placed.linked is True
    layer = next(n for n in placed.graph.nodes if n.node_id == "lyr-1")
    px = next(n for n in placed.graph.nodes if n.node_id == "px-lyr-1")
    assert layer.position == {"x": 600.0, "y": 40.0}
    assert px.position == {"x": 920.0, "y": 40.0}
    assert not in_blank_canvas(layer.position) and not in_blank_canvas(px.position)
    assert any(e.source == "lyr-1" and e.target == "px-lyr-1" and e.sign == 1 and e.weight == 1.0 for e in placed.graph.edges)
    second = apply_cms_layer_suggestion(placed.graph, tpl, id_factory=_ids("reuse2"))
    assert second.changed is False
    assert len([n for n in second.graph.nodes if n.node_type == NodeType.PRICING_OUTPUT]) == 1


def test_programme_without_layers_suggests_one_paired_pricing_output():
    c = _create("Fallback", [{"limit": 15, "attachment": 15, "depositPremium": 2.0}])
    full = ContractFull.model_validate(c)
    bare = full.graph.model_copy(deep=True)
    layer_ids = {n.node_id for n in bare.nodes if n.node_type == NodeType.OCC_XL}
    bare.nodes = [n for n in bare.nodes if n.node_id not in layer_ids and n.node_type != NodeType.PRICING_OUTPUT]
    bare.edges = [e for e in bare.edges if e.source not in layer_ids and e.target not in layer_ids]
    tpls = build_graph_templates(full.model_copy(update={"graph": bare}))
    fb = next(t for t in tpls if t.template_id == f"cms-layer-{full.programme_id}")
    assert fb.placement == "NEW_COPY"
    assert fb.pricing_output is not None
    assert fb.pricing_output.params["contractId"] == full.programme_id
    assert "layerNodeId" not in fb.pricing_output.params

    placed = apply_cms_layer_suggestion(bare, fb, id_factory=_ids("fb"))
    assert placed.linked is True
    px = next(n for n in placed.graph.nodes if n.node_id == placed.pricing_node_id)
    layer = next(n for n in placed.graph.nodes if n.node_id == placed.layer_node_id)
    assert px.params["layerNodeId"] == placed.layer_node_id
    assert px.params["contractId"] == full.programme_id
    assert not in_blank_canvas(layer.position)
    assert not in_blank_canvas(px.position)
    second = apply_cms_layer_suggestion(placed.graph, fb, id_factory=_ids("fb2"))
    assert second.changed is False


def test_suggestion_tree_payload_includes_the_pricing_node():
    """The palette tree is drawn from this payload, before the suggestion is placed."""
    c = _create("Tree", [
        {"layerCode": "L1", "name": "Cat XL", "limit": 40, "attachment": 60, "depositPremium": 8},
    ])
    pid = c["programmeId"]
    suggestions = _cms_layers(_templates(pid), pid)
    assert suggestions
    for tpl in suggestions:
        pricing = tpl["pricingOutput"]
        assert pricing["nodeType"] == "PRICING_OUTPUT"
        assert pricing["label"]
        assert pricing["params"]["contractId"] == pid
        assert pricing["params"]["layerNodeId"] == tpl["layerNodeId"]


def test_click_or_drag_places_the_suggestion_already_joined():
    """Click and drag share apply_cms_layer_suggestion, so the pair never lands disconnected."""
    c = _create("Joined", [
        {"layerCode": "L1", "name": "Cat XL", "limit": 40, "attachment": 60, "depositPremium": 8},
    ])
    pid = c["programmeId"]
    tpl = GraphTemplate.model_validate(_cms_layers(_templates(pid), pid)[0])
    bare = GraphSpec.model_validate(c["graph"])
    layer_ids = {n.node_id for n in bare.nodes if n.node_type in {NodeType.OCC_XL, NodeType.PRICING_OUTPUT}}
    bare.nodes = [n for n in bare.nodes if n.node_id not in layer_ids]
    bare.edges = [e for e in bare.edges if e.source not in layer_ids and e.target not in layer_ids]
    for _gesture in ("click", "drag"):
        placed = apply_cms_layer_suggestion(bare, tpl, id_factory=_ids(_gesture))
        assert placed.changed is True
        assert any(
            e.source == placed.layer_node_id and e.target == placed.pricing_node_id
            for e in placed.graph.edges
        )
        layer = next(n for n in placed.graph.nodes if n.node_id == placed.layer_node_id)
        px = next(n for n in placed.graph.nodes if n.node_id == placed.pricing_node_id)
        assert not in_blank_canvas(layer.position)
        assert not in_blank_canvas(px.position)
        assert not (BLANK_CANVAS_X[0] <= px.position["x"] <= BLANK_CANVAS_X[1] and 80 <= px.position["y"] <= 260)
