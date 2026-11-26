"""Structuring Studio palette: blank-canvas nodes and CMS layer suggestions.

A CMS layer suggestion always carries exactly one Pricing Output. Dropping it
places that pair on the layer column, never in the blank-canvas scatter box,
and keeps the Pricing Output 1:1 with the contracted layer.

The placement rules are mirrored in `frontend/src/utils/cmsLayerSuggestion.ts`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field

from app.models.common import CamelModel
from app.models.domain import GraphEdgeSpec, GraphNodeSpec, GraphSpec
from app.models.enums import NodeType, StructureType
from app.models.phase1 import ContractFull, ContractLayer
from app.services.contracts import _next_layer_code, contract_layers
from app.services.node_labels import smart_label
from app.services.pricing_bind import ALREADY_BOUND

# Blank-canvas palette drops use this box (see StructuringStudio `addTemplateNode`).
# CMS layer suggestions must not be placed inside it.
BLANK_CANVAS_X = (420.0, 560.0)
BLANK_CANVAS_Y = (80.0, 260.0)
LAYER_COLUMN_X = 600.0
LAYER_ROW_GAP = 140.0
PRICING_OFFSET_X = 320.0
PRICING_OFFSET_Y = 0.0
SUGGESTED_FROM_PARAM = "suggestedFrom"

LAYER_NODE_TYPES = frozenset({
    "OCC_XL",
    "AGG_XL",
    "QUOTA_SHARE",
    "AGG_QUOTA_SHARE",
    "SURPLUS_SHARE",
    "CASCADING_TOWER",
    "ILW_OPTION",
})

class GraphTemplateOffset(CamelModel):
    x: float = PRICING_OFFSET_X
    y: float = PRICING_OFFSET_Y


class GraphTemplatePricingOutput(CamelModel):
    """The single Pricing Output that belongs to a CMS layer suggestion."""

    node_type: Literal["PRICING_OUTPUT"] = "PRICING_OUTPUT"
    label: str = "Pricing"
    params: dict[str, Any] = Field(default_factory=dict)
    offset: GraphTemplateOffset = Field(default_factory=GraphTemplateOffset)


class GraphTemplate(CamelModel):
    template_id: str
    category: str
    source: Literal["CMS", "BLANK"]
    label: str
    node_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    layer_code: str | None = None
    layer_node_id: str | None = None
    contract_id: str | None = None
    # EXISTING_LAYER reuses this programme's layer node. NEW_COPY drops a fresh
    # layer (prior-year or a programme that has no layer node yet). BLANK is the
    # unlinked palette and is never used for a CMS layer.
    placement: Literal["EXISTING_LAYER", "NEW_COPY", "BLANK"] = "BLANK"
    position: dict[str, float] | None = None
    pricing_output: GraphTemplatePricingOutput | None = None
    # True when this suggestion's pricing output is already bound, so it is not offered.
    pricing_bound: bool = False


@dataclass
class CmsLayerPlacement:
    graph: GraphSpec
    selected_node_id: str
    added_node_ids: list[str] = field(default_factory=list)
    added_edge_ids: list[str] = field(default_factory=list)
    layer_node_id: str = ""
    pricing_node_id: str = ""
    linked: bool = False
    changed: bool = False


def in_blank_canvas(position: dict[str, Any] | None) -> bool:
    if not position:
        return False
    x = float(position.get("x", 0))
    y = float(position.get("y", 0))
    return BLANK_CANVAS_X[0] <= x <= BLANK_CANVAS_X[1] and BLANK_CANVAS_Y[0] <= y <= BLANK_CANVAS_Y[1]


def _uuid_factory(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _link_key(params: dict[str, Any]) -> tuple[str, str] | None:
    cid = str(params.get("contractId") or "").strip()
    lid = str(params.get("layerNodeId") or "").strip()
    return (cid, lid) if cid and lid else None


def _xy(position: dict[str, Any] | None, default_x: float = 0.0, default_y: float = 0.0) -> dict[str, float]:
    src = position or {}
    return {"x": float(src.get("x", default_x)), "y": float(src.get("y", default_y))}


def _used_ids(graph: GraphSpec) -> set[str]:
    return {n.node_id for n in graph.nodes} | {e.edge_id for e in graph.edges}


def _fresh_id(graph: GraphSpec, preferred: str, factory: Callable[[str], str], prefix: str) -> str:
    used = _used_ids(graph)
    if preferred and preferred not in used:
        return preferred
    nid = factory(prefix)
    while not nid or nid in used:
        nid = factory(prefix)
    return nid


def _layer_nodes(graph: GraphSpec) -> list[GraphNodeSpec]:
    return [n for n in graph.nodes if getattr(n.node_type, "value", n.node_type) in LAYER_NODE_TYPES]


def _column_x(graph: GraphSpec) -> float:
    layers = _layer_nodes(graph)
    if not layers:
        return LAYER_COLUMN_X
    return float(layers[0].position.get("x", LAYER_COLUMN_X))


def _next_row_y(graph: GraphSpec) -> float:
    layers = _layer_nodes(graph)
    if not layers:
        return 40.0
    return max(float(n.position.get("y", 0)) for n in layers) + LAYER_ROW_GAP


def beside(layer_pos: dict[str, Any], offset: tuple[float, float]) -> dict[str, float]:
    """Pricing-node position on the same row as the layer, outside the blank-canvas box."""
    pos = {"x": float(layer_pos.get("x", 0)) + offset[0], "y": float(layer_pos.get("y", 0)) + offset[1]}
    if in_blank_canvas(pos):
        pos = {"x": BLANK_CANVAS_X[1] + 40.0, "y": float(layer_pos.get("y", 0))}
        if in_blank_canvas(pos):
            pos = {"x": pos["x"], "y": BLANK_CANVAS_Y[1] + 40.0}
    return pos


def _layer_anchor(graph: GraphSpec, anchor: dict[str, Any] | None, offset: tuple[float, float]) -> dict[str, float]:
    """A new layer position on the structure column, not in the blank-canvas box."""
    pos = _xy(anchor, _column_x(graph), _next_row_y(graph)) if anchor else {"x": _column_x(graph), "y": _next_row_y(graph)}
    if in_blank_canvas(pos) or in_blank_canvas(beside(pos, offset)):
        pos = {"x": LAYER_COLUMN_X, "y": float(pos["y"])}
    if in_blank_canvas(pos) or in_blank_canvas(beside(pos, offset)):
        pos = {"x": LAYER_COLUMN_X, "y": BLANK_CANVAS_Y[1] + LAYER_ROW_GAP}
    return pos


def _contract_code(contract: ContractFull) -> str:
    return (contract.profile.unique_code or "").strip() or contract.programme_id


def _linked_label(contract: ContractFull, layer_code: str, layer_name: str) -> str:
    summary = " · ".join(part for part in (layer_code, layer_name) if part)
    return " · ".join(part for part in (_contract_code(contract), summary) if part)


def _pricing_output(contract: ContractFull, layer_code: str, layer_name: str, layer_node_id: str | None) -> GraphTemplatePricingOutput:
    params: dict[str, Any] = {
        "contractId": contract.programme_id,
        "labelName": "Pricing",
        "linkedLabel": _linked_label(contract, layer_code, layer_name),
    }
    if layer_node_id:
        params["layerNodeId"] = layer_node_id
    return GraphTemplatePricingOutput(params=params)


def _cms_layer_template(
    contract: ContractFull,
    layer: ContractLayer,
    node: GraphNodeSpec | None,
    *,
    template_id: str,
    category: str,
    placement: Literal["EXISTING_LAYER", "NEW_COPY"],
) -> GraphTemplate:
    params = dict(node.params) if node else {}
    params["layerCode"] = layer.layer_code
    return GraphTemplate(
        template_id=template_id,
        layer_code=layer.layer_code,
        layer_node_id=layer.node_id,
        contract_id=contract.programme_id,
        category=category,
        source="CMS",
        label=layer.label,
        node_type=layer.node_type,
        params=params,
        placement=placement,
        position=_xy(node.position) if node and placement == "EXISTING_LAYER" else None,
        pricing_output=_pricing_output(contract, layer.layer_code, layer.name or layer.label, layer.node_id),
    )


def _fallback_layer_template(contract: ContractFull) -> GraphTemplate:
    struct_node = {
        StructureType.OCC_XL: "OCC_XL",
        StructureType.AGG_XL: "AGG_XL",
        StructureType.QUOTA_SHARE: "AGG_QUOTA_SHARE",
        StructureType.CASCADING: "CASCADING_TOWER",
        StructureType.ILW: "ILW_OPTION",
    }.get(contract.structure, "OCC_XL")
    if struct_node == "AGG_XL":
        params: dict[str, Any] = {
            "layerCode": "L1",
            "aggLimit": contract.limit,
            "aggAttachment": contract.attachment,
            "perEventFranchise": 0.0,
            "perEventCap": None,
            "depositPremium": contract.deposit_premium,
            "placedShare": contract.signed_line,
        }
    elif struct_node == "AGG_QUOTA_SHARE":
        params = {
            "layerCode": "L1",
            "cessionShare": contract.signed_line,
            "eventCap": contract.limit,
            "aggCap": contract.limit,
            "depositPremium": contract.deposit_premium,
            "placedShare": 1.0,
        }
    else:
        params = {
            "layerCode": "L1",
            "occLimit": contract.limit,
            "occAttachment": contract.attachment,
            "occFranchise": 0.0,
            "reinstatements": 1.0,
            "ripRate": 1.0,
            "depositPremium": contract.deposit_premium,
            "placedShare": contract.signed_line,
        }
    return GraphTemplate(
        template_id=f"cms-layer-{contract.programme_id}",
        layer_code="L1",
        contract_id=contract.programme_id,
        category="CMS Layer · L1",
        source="CMS",
        label=f"{contract.programme_name} ({contract.limit:g}m xs {contract.attachment:g}m)",
        node_type=struct_node,
        params=params,
        placement="NEW_COPY",
        pricing_output=_pricing_output(contract, "L1", contract.programme_name, None),
    )


def _blank_templates() -> list[GraphTemplate]:
    rows: list[tuple[str, str, str, str, dict[str, Any]]] = [
        ("blank-occ-xl", "Financial Terms", "Occurrence XL", "OCC_XL", {
            "occLimit": "", "occAttachment": "", "occFranchise": 0.0, "reinstatements": 0.0,
            "ripRate": 0.0, "depositPremium": "", "placedShare": 1.0,
        }),
        ("blank-agg-xl", "Financial Terms", "Aggregate XL", "AGG_XL", {
            "aggLimit": "", "aggAttachment": "", "perEventFranchise": 0.0, "perEventCap": "",
            "depositPremium": "", "placedShare": 1.0,
        }),
        ("blank-qs", "Financial Terms", "Quota Share", "AGG_QUOTA_SHARE", {
            "cessionShare": "", "eventCap": "", "aggCap": "", "depositPremium": "", "placedShare": 1.0,
        }),
        ("blank-surplus", "Financial Terms", "Surplus Share", "SURPLUS_SHARE", {
            "retentionLine": "", "numLines": "", "depositPremium": "", "placedShare": 1.0,
        }),
        ("blank-cascading", "Financial Terms", "Cascading Tower", "CASCADING_TOWER", {
            "occLimit": "", "depositPremium": "", "placedShare": 1.0, "layers": [],
        }),
        ("blank-flt-rp", "Filters", "Zone / Peril Filter", "REGION_PERIL_FILTER", {
            "zones": [], "perils": [], "regionPerils": [],
        }),
        ("blank-flt-order", "Filters", "Event Order Filter", "EVENT_ORDER_FILTER", {
            "minOrder": 2, "maxOrder": "", "minLossThreshold": 0.0,
        }),
        ("blank-combo", "Transforms", "Combination (+/- Inuring)", "COMBINATION", {}),
        ("blank-out-pricing", "Outputs", "Pricing Output", "PRICING_OUTPUT", {}),
        ("blank-out-yelt", "Outputs", "YELT Output", "YELT_OUTPUT", {"view": "ADJUSTED"}),
    ]
    return [
        GraphTemplate(
            template_id=template_id,
            category=category,
            source="BLANK",
            label=label,
            node_type=node_type,
            params=params,
            placement="BLANK",
        )
        for template_id, category, label, node_type, params in rows
    ]


def build_graph_templates(
    contract: ContractFull | None,
    prior: ContractFull | None = None,
    bound_slots: set[tuple[str, str]] | None = None,
) -> list[GraphTemplate]:
    """Blank-canvas node types, plus one CMS suggestion per contracted layer.

    A pricing output that is already bound is not offered again on a prior-year copy.
    """
    bound = bound_slots or set()
    templates: list[GraphTemplate] = []
    if contract is not None:
        layers = contract_layers(contract)
        nodes_by_id = {n.node_id: n for n in contract.graph.nodes}
        if layers:
            for layer in layers:
                templates.append(_cms_layer_template(
                    contract,
                    layer,
                    nodes_by_id.get(layer.node_id),
                    template_id=f"cms-layer-{contract.programme_id}-{layer.node_id}",
                    category=f"CMS Layer · {layer.layer_code}",
                    placement="EXISTING_LAYER",
                ))
        else:
            templates.append(_fallback_layer_template(contract))
        if contract.region_perils:
            templates.append(GraphTemplate(
                template_id=f"cms-perils-{contract.programme_id}",
                category="Suggested from CMS",
                source="CMS",
                label=f"Programme Perils ({', '.join(contract.region_perils)})",
                node_type="REGION_PERIL_FILTER",
                params={"regionPerils": list(contract.region_perils)},
            ))
        if prior is not None:
            prior_nodes = {n.node_id: n for n in prior.graph.nodes}
            for layer in contract_layers(prior):
                suggestion = _cms_layer_template(
                    prior,
                    layer,
                    prior_nodes.get(layer.node_id),
                    template_id=f"cms-prior-{prior.programme_id}-{layer.node_id}",
                    category=f"Prior UY ({prior.treaty_year}) · {layer.layer_code}",
                    placement="NEW_COPY",
                )
                if (prior.programme_id, layer.node_id) in bound:
                    suggestion = suggestion.model_copy(update={"pricing_output": None, "pricing_bound": True})
                templates.append(suggestion)
    templates.extend(_blank_templates())
    return templates


def apply_cms_layer_suggestion(
    graph: GraphSpec,
    template: GraphTemplate,
    *,
    claimed_elsewhere: set[tuple[str, str]] | None = None,
    id_factory: Callable[[str], str] | None = None,
) -> CmsLayerPlacement:
    """Place a CMS layer suggestion with exactly one Pricing Output beside it.

    Assumptions, chosen so each CMS layer keeps a single Pricing Output:
    - A layer that already feeds a Pricing Output keeps that node. It is not
      duplicated and an unlinked feeder is not relinked, so a legacy tower does
      not flip into selective rollup merely by clicking a layer that is already paired.
    - When the layer has no Pricing Output, one is added on the same row. It
      takes the 1:1 CMS link when that `(contractId, layerNodeId)` slot is free.
    - If another programme already holds the link, placement is refused. A bound
      pricing output is not created again for another layer.
    - Prior-year suggestions (`NEW_COPY`) mint a new layer id so they cannot
      collide with this programme's nodes. A second click selects the copy.
    """
    pricing = template.pricing_output
    if pricing is None or template.source != "CMS" or template.node_type not in LAYER_NODE_TYPES:
        raise ValueError("Only a CMS layer suggestion with a pricing output can be placed on the canvas")

    g = graph.model_copy(deep=True)
    factory = id_factory or _uuid_factory
    offset = (float(pricing.offset.x), float(pricing.offset.y))
    link_params = dict(pricing.params)
    contract_id = str(link_params.get("contractId") or template.contract_id or "").strip()
    contracted_layer_id = str(link_params.get("layerNodeId") or template.layer_node_id or "").strip()
    external = set(claimed_elsewhere or ())
    added_nodes: list[str] = []
    added_edges: list[str] = []

    def linked_node(cid: str, lid: str) -> GraphNodeSpec | None:
        if not cid or not lid:
            return None
        for node in g.nodes:
            if node.node_type == NodeType.PRICING_OUTPUT and _link_key(node.params) == (cid, lid):
                return node
        return None

    def slot_taken(cid: str, lid: str) -> bool:
        if not cid or not lid:
            return False
        return (cid, lid) in external or linked_node(cid, lid) is not None

    def fed_by(layer_id: str) -> GraphNodeSpec | None:
        targets = {e.target for e in g.edges if e.source == layer_id}
        for node in g.nodes:
            if node.node_id in targets and node.node_type == NodeType.PRICING_OUTPUT:
                return node
        return None

    def ensure_edge(source: str, target: str) -> None:
        if any(e.source == source and e.target == target for e in g.edges):
            return
        edge_id = _fresh_id(g, f"e-px-{target}", factory, "e")
        g.edges.append(GraphEdgeSpec(edge_id=edge_id, source=source, target=target, sign=1, weight=1.0))
        added_edges.append(edge_id)

    def add_pricing(layer_id: str, layer_pos: dict[str, Any], link_to: str | None) -> GraphNodeSpec:
        if contract_id and link_to and slot_taken(contract_id, link_to):
            raise ValueError(ALREADY_BOUND)
        use_link = bool(contract_id and link_to)
        if use_link:
            params = dict(link_params)
            params["contractId"] = contract_id
            params["layerNodeId"] = link_to
            params.pop("unlinked", None)
            params.setdefault("labelName", "Pricing")
        else:
            params = {"labelName": "Pricing"}
        node_id = _fresh_id(g, f"px-{layer_id}", factory, "px")
        node = GraphNodeSpec(
            node_id=node_id,
            node_type=NodeType.PRICING_OUTPUT,
            label=smart_label("PRICING_OUTPUT", params, pricing.label or "Pricing"),
            params=params,
            position=beside(layer_pos, offset),
        )
        g.nodes.append(node)
        added_nodes.append(node_id)
        ensure_edge(layer_id, node_id)
        return node

    def add_layer(node_id: str, params: dict[str, Any], position: dict[str, float]) -> GraphNodeSpec:
        node = GraphNodeSpec(
            node_id=node_id,
            node_type=NodeType(template.node_type),
            label=smart_label(template.node_type, params, template.label),
            params=params,
            position=position,
        )
        g.nodes.append(node)
        added_nodes.append(node_id)
        return node

    def finish(layer_id: str, pricing_id: str) -> CmsLayerPlacement:
        px = next(n for n in g.nodes if n.node_id == pricing_id)
        return CmsLayerPlacement(
            graph=g,
            selected_node_id=pricing_id,
            added_node_ids=list(added_nodes),
            added_edge_ids=list(added_edges),
            layer_node_id=layer_id,
            pricing_node_id=pricing_id,
            linked=_link_key(px.params) is not None,
            changed=bool(added_nodes or added_edges),
        )

    if template.placement == "EXISTING_LAYER" and contracted_layer_id:
        layer = next((n for n in g.nodes if n.node_id == contracted_layer_id), None)
        if layer is not None and getattr(layer.node_type, "value", layer.node_type) in LAYER_NODE_TYPES:
            fed = fed_by(layer.node_id)
            if fed is not None:
                return finish(layer.node_id, fed.node_id)
            existing = linked_node(contract_id, contracted_layer_id)
            if existing is not None:
                ensure_edge(layer.node_id, existing.node_id)
                return finish(layer.node_id, existing.node_id)
            created = add_pricing(layer.node_id, layer.position, contracted_layer_id)
            return finish(layer.node_id, created.node_id)

        existing = linked_node(contract_id, contracted_layer_id)
        if existing is not None:
            left_of_pricing = {
                "x": float(existing.position.get("x", 0)) - offset[0],
                "y": float(existing.position.get("y", 0)) - offset[1],
            }
            # Keep the restored layer with its pricing node, and pull the pair out of the blank box.
            if in_blank_canvas(existing.position) or in_blank_canvas(left_of_pricing):
                layer_pos = _layer_anchor(g, None, offset)
                existing.position = beside(layer_pos, offset)
            else:
                layer_pos = left_of_pricing
            restored_id = contracted_layer_id if contracted_layer_id not in _used_ids(g) else _fresh_id(g, "", factory, "lyr")
            restored = add_layer(restored_id, dict(template.params), layer_pos)
            ensure_edge(restored.node_id, existing.node_id)
            return finish(restored.node_id, existing.node_id)

        layer_pos = _layer_anchor(g, template.position, offset)
        restored = add_layer(_fresh_id(g, contracted_layer_id, factory, "lyr"), dict(template.params), layer_pos)
        created = add_pricing(restored.node_id, restored.position, contracted_layer_id)
        return finish(restored.node_id, created.node_id)

    existing_copy = next(
        (
            n for n in g.nodes
            if str(n.params.get(SUGGESTED_FROM_PARAM) or "") == template.template_id
            and getattr(n.node_type, "value", n.node_type) in LAYER_NODE_TYPES
        ),
        None,
    )
    if existing_copy is not None:
        fed = fed_by(existing_copy.node_id)
        if fed is not None:
            return finish(existing_copy.node_id, fed.node_id)
        created = add_pricing(existing_copy.node_id, existing_copy.position, contracted_layer_id or existing_copy.node_id)
        return finish(existing_copy.node_id, created.node_id)

    layer_pos = _layer_anchor(g, None, offset)
    params = dict(template.params)
    params[SUGGESTED_FROM_PARAM] = template.template_id
    params["layerCode"] = _assign_layer_code(g, str(params.get("layerCode") or template.layer_code or ""))
    copy = add_layer(_fresh_id(g, "", factory, "lyr"), params, layer_pos)
    created = add_pricing(copy.node_id, copy.position, contracted_layer_id or copy.node_id)
    return finish(copy.node_id, created.node_id)


def _assign_layer_code(graph: GraphSpec, candidate: str) -> str:
    used = {str(n.params.get("layerCode") or "").strip().lower() for n in _layer_nodes(graph)}
    used.discard("")
    code = candidate.strip()
    if code and code.lower() not in used:
        return code
    return _next_layer_code(used)
