"""Output-node services: Pricing Output contract links and YELT Output extraction/export."""

from __future__ import annotations

import io
from collections.abc import Callable

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from app.engine.pricing_link import LAYER_NODE_TYPES, PricingLink
from app.engine.sim_generator import REGION_PERILS, YeltTable
from app.models.domain import GraphSpec
from app.models.enums import NodeType
from app.models.phase1 import ContractFull, ContractLayer
from app.services.contracts import contract_layers
from app.services.lineage import canonical_hash


def _link_from_layer(source: str, contract: ContractFull, layer: ContractLayer) -> PricingLink:
    return PricingLink(
        source=source,
        contract_id=contract.programme_id,
        contract_code=contract.profile.unique_code or contract.programme_id,
        contract_name=f"{contract.cedent_name} — {contract.programme_name}",
        layer_node_id=layer.node_id,
        layer_label=layer.label,
        limit=layer.limit,
        attachment=layer.attachment,
        premium=layer.deposit_premium,
        written_line=layer.written_line,
        signed_line=layer.signed_line,
        brokerage_pct=layer.brokerage_pct,
        technical_rol=layer.technical_rol,
    )


def resolve_pricing_links(
    graph: GraphSpec,
    current: ContractFull | None,
    get_contract: Callable[[str], ContractFull | None],
) -> dict[str, PricingLink]:
    """Each Pricing Output takes economics from the contract layer chosen on the node, or else from
    the layer node feeding it on this programme."""
    nodes_by_id = {n.node_id: n for n in graph.nodes}
    links: dict[str, PricingLink] = {}
    for node in graph.nodes:
        if node.node_type != NodeType.PRICING_OUTPUT:
            continue
        contract_id = node.params.get("contractId")
        if contract_id:
            contract = current if current and current.programme_id == contract_id else get_contract(str(contract_id))
            if contract is None:
                continue
            layers = contract_layers(contract)
            if not layers:
                continue
            wanted = node.params.get("layerNodeId")
            layer = next((l for l in layers if l.node_id == wanted), layers[0])
            links[node.node_id] = _link_from_layer("LINKED", contract, layer)
            continue
        if current is None:
            continue
        upstream = [
            e.source for e in graph.edges
            if e.target == node.node_id and nodes_by_id[e.source].node_type in LAYER_NODE_TYPES
        ]
        if not upstream:
            continue
        layers = {l.node_id: l for l in contract_layers(current.model_copy(update={"graph": graph}))}
        if upstream[0] in layers:
            links[node.node_id] = _link_from_layer("UPSTREAM", current, layers[upstream[0]])
    return links


def node_yelt_table(
    yelt: YeltTable,
    events: np.ndarray,
    *,
    sim_set_id: str,
    name: str,
    lineage_key: dict,
) -> YeltTable:
    """Event-level losses of a node as a canonical YELT (zero-loss rows dropped)."""
    mask = events > 0.0
    losses = np.ascontiguousarray(events[mask], dtype=np.float64)
    return YeltTable(
        sim_set_id=sim_set_id,
        name=name,
        event_catalog_id=yelt.event_catalog_id,
        num_trials=yelt.num_trials,
        currency=yelt.currency,
        trial_ids=np.ascontiguousarray(yelt.trial_ids[mask]),
        event_ids=np.ascontiguousarray(yelt.event_ids[mask]),
        day_of_year=np.ascontiguousarray(yelt.day_of_year[mask]),
        rp_ids=np.ascontiguousarray(yelt.rp_ids[mask]),
        losses=losses,
        content_hash=canonical_hash({**lineage_key, "events": int(len(losses)), "sum": round(float(losses.sum()), 6)}),
    )


def _region_peril_names(rp_ids: np.ndarray) -> list[str]:
    names = np.array(REGION_PERILS + ["UNKNOWN"], dtype=object)
    return names[np.where((rp_ids >= 0) & (rp_ids < len(REGION_PERILS)), rp_ids, len(REGION_PERILS))].tolist()


def yelt_export_bytes(table: YeltTable, fmt: str) -> bytes:
    pa_table = pa.table({
        "trial_id": pa.array(table.trial_ids, type=pa.uint32()),
        "event_id": pa.array(table.event_ids, type=pa.uint64()),
        "day_of_year": pa.array(table.day_of_year, type=pa.uint16()),
        "region_peril": pa.array(_region_peril_names(table.rp_ids), type=pa.string()),
        "loss_amount": pa.array(table.losses, type=pa.float64()),
    })
    buf = io.BytesIO()
    if fmt == "parquet":
        pq.write_table(pa_table, buf, compression="zstd")
    else:
        pacsv.write_csv(pa_table, buf)
    return buf.getvalue()


def yelt_summary(table: YeltTable, top_n: int = 8) -> dict:
    losses = table.losses
    n = table.num_trials
    annual = np.bincount(table.trial_ids.astype(np.int64) - 1, weights=losses, minlength=n) if len(losses) else np.zeros(n)
    order = np.argsort(losses)[::-1][:top_n]
    names = _region_peril_names(table.rp_ids[order]) if len(order) else []
    return {
        "numTrials": n,
        "eventRows": int(len(losses)),
        "trialsWithLoss": int(np.count_nonzero(annual)),
        "aal": round(float(annual.mean()), 6) if n else 0.0,
        "sd": round(float(annual.std()), 6) if n else 0.0,
        "maxEvent": round(float(losses.max()), 6) if len(losses) else 0.0,
        "maxYear": round(float(annual.max()), 6) if n else 0.0,
        "currency": table.currency,
        "topEvents": [
            {
                "trialId": int(table.trial_ids[i]),
                "eventId": int(table.event_ids[i]),
                "dayOfYear": int(table.day_of_year[i]),
                "regionPeril": names[k],
                "loss": round(float(losses[i]), 6),
            }
            for k, i in enumerate(order)
        ],
    }
