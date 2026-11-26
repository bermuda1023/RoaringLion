"""Pricing Output connections: one incoming edge, and a same-programme bind follows a reconnect.

Mirrored in ``frontend/src/utils/pricingBind.ts``. A saved graph may still price a CMS
layer from a different feeder when ``layerNodeId`` is left on that layer; the studio
moves the bind when the user disconnects and reconnects.
"""

from __future__ import annotations

from app.models.domain import GraphEdgeSpec, GraphNodeSpec, GraphSpec
from app.models.enums import NodeType
from app.services.node_labels import smart_label

LAYER_NODE_TYPES = frozenset({
    NodeType.OCC_XL,
    NodeType.AGG_XL,
    NodeType.QUOTA_SHARE,
    NodeType.AGG_QUOTA_SHARE,
    NodeType.SURPLUS_SHARE,
    NodeType.CASCADING_TOWER,
    NodeType.ILW_OPTION,
})

ALREADY_LINKED = (
    "This Pricing Output is already linked to a layer. "
    "Disconnect it before connecting another. One pricing output binds one layer."
)

LAYER_ALREADY_BOUND = (
    "That layer already has a Pricing Output. "
    "Disconnect it there first — one pricing output binds one layer."
)


def _is_layer(node: GraphNodeSpec) -> bool:
    return node.node_type in LAYER_NODE_TYPES


def _claim(params: dict) -> tuple[str, str] | None:
    cid = str(params.get("contractId") or "").strip()
    lid = str(params.get("layerNodeId") or "").strip()
    return (cid, lid) if cid and lid else None


def _layer_summary(layer: GraphNodeSpec) -> str:
    code = str(layer.params.get("layerCode") or "").strip()
    name = str(layer.params.get("labelName") or "").strip() or layer.label
    return " · ".join(part for part in (code, name) if part)


def _moved_label(existing: str, layer: GraphNodeSpec) -> str:
    head = existing.split(" · ")[0].strip() if existing else ""
    summary = _layer_summary(layer)
    return " · ".join(part for part in (head, summary) if part)


def validate_pricing_output_single_connection(graph: GraphSpec) -> None:
    """A Pricing Output can be connected to one layer. A second incoming edge is rejected."""
    labels = {n.node_id: n.label for n in graph.nodes if n.node_type == NodeType.PRICING_OUTPUT}
    counts = dict.fromkeys(labels, 0)
    for edge in graph.edges:
        if edge.target in counts:
            counts[edge.target] += 1
    for node_id, count in counts.items():
        if count > 1:
            raise ValueError(
                f"Pricing Output '{labels[node_id]}' is already linked to a layer. "
                "Disconnect it before connecting another. One pricing output binds one layer."
            )


def connect_nodes(
    graph: GraphSpec,
    programme_id: str,
    source: str,
    target: str,
    edge_id: str,
    *,
    claimed_elsewhere: set[tuple[str, str]] | None = None,
) -> GraphSpec:
    """Connect two nodes. A Pricing Output that already has an incoming edge is refused.

    When the output is linked to ``programme_id`` and the source is a free layer, the
    bind moves: ``layerNodeId`` becomes that layer, so the previous layer loses it.
    """
    if not source or not target or source == target:
        raise ValueError("Choose two different nodes to connect")
    g = graph.model_copy(deep=True)
    by_id = {n.node_id: n for n in g.nodes}
    src = by_id.get(source)
    dst = by_id.get(target)
    if src is None or dst is None:
        raise ValueError("That connection does not match a node on this graph")
    if any(e.source == source and e.target == target for e in g.edges):
        return g
    if dst.node_type == NodeType.PRICING_OUTPUT and any(e.target == target for e in g.edges):
        raise ValueError(ALREADY_LINKED)

    if dst.node_type == NodeType.PRICING_OUTPUT and _is_layer(src):
        cid = str(dst.params.get("contractId") or "").strip()
        if cid and cid == programme_id:
            external = claimed_elsewhere or set()
            taken = (cid, src.node_id) in external or any(
                n.node_id != dst.node_id and n.node_type == NodeType.PRICING_OUTPUT and _claim(n.params) == (cid, src.node_id)
                for n in g.nodes
            )
            if taken:
                raise ValueError(LAYER_ALREADY_BOUND)
            params = dict(dst.params)
            params["layerNodeId"] = src.node_id
            params.pop("unlinked", None)
            params["linkedLabel"] = _moved_label(str(params.get("linkedLabel") or ""), src)
            dst.params = params
            dst.label = smart_label(dst.node_type, params, dst.label)

    g.edges.append(GraphEdgeSpec(edge_id=edge_id, source=source, target=target, sign=1, weight=1.0))
    return g
