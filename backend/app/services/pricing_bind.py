"""Pricing Output connections: one incoming edge. The CMS layer identity stays locked.

Mirrored in ``frontend/src/utils/pricingBind.ts``. Disconnecting and reconnecting moves
which graph layer feeds the output. ``contractId`` and ``layerNodeId`` stay on the CMS
layer that was bought, and rollup keeps that layer's terms while using the attached
graph layer's losses.
"""

from __future__ import annotations

from app.models.domain import GraphEdgeSpec, GraphSpec
from app.models.enums import NodeType

ALREADY_LINKED = (
    "This Pricing Output is already linked to a layer. "
    "Disconnect it before connecting another. One pricing output binds one layer."
)

ALREADY_BOUND = "This Pricing Output is already bound and cannot be used for another layer."

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


def _claim(params: dict) -> tuple[str, str] | None:
    cid = str(params.get("contractId") or "").strip()
    lid = str(params.get("layerNodeId") or "").strip()
    return (cid, lid) if cid and lid else None


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

    Reconnecting does not change ``contractId`` or ``layerNodeId``. If that CMS identity
    is already bound on another programme, the connection is refused. ``programme_id``
    stays in the signature so the studio helper and this function match.
    """
    del programme_id
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
    if dst.node_type == NodeType.PRICING_OUTPUT:
        claim = _claim(dst.params)
        if claim and claim in (claimed_elsewhere or set()):
            raise ValueError(ALREADY_BOUND)

    g.edges.append(GraphEdgeSpec(edge_id=edge_id, source=source, target=target, sign=1, weight=1.0))
    return g
