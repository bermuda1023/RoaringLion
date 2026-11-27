"""Point-in-time UNL portfolio snapshot builder for normal retro hedges (PORTFOLIO_ROLLUP source nodes).

Computes the event-level UNL indemnity loss stream of our own assumed book as of a given
point-in-time date (`asOfDate`), on the shared event catalogue so all downstream filters
(peril, region, event order, date window) and financial terms work identically to cedent deals.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.engine.pricing_link import (
    LAYER_NODE_TYPES,
    ROLLUP_TERM_TYPES,
    _layer_share_and_premium,
    graph_uses_explicit_pricing_links,
)
from app.engine.sim_generator import REGION_PERILS, YeltTable
from app.models.enums import (
    ContractDirection,
    InstrumentType,
    NodeType,
    ProgrammeStatus,
    StructureType,
    ViewOfRisk,
    is_hedge_direction,
)
from app.services.lineage import LineageRegistry, canonical_hash
from app.services.loss_files import files_from_keys


def is_ilw_programme(structure: StructureType | str, instrument: InstrumentType | str) -> bool:
    s = structure.value if isinstance(structure, StructureType) else str(structure)
    i = instrument.value if isinstance(instrument, InstrumentType) else str(instrument)
    return s == StructureType.ILW.value or i == InstrumentType.INDUSTRY_INDEX.value


def is_unl_retro_hedge(
    direction: ContractDirection | str,
    structure: StructureType | str,
    instrument: InstrumentType | str,
) -> bool:
    return is_hedge_direction(direction) and not is_ilw_programme(structure, instrument)


def _in_force_on(inception: str | None, expiry: str | None, as_of_date: str) -> bool:
    if inception and as_of_date < inception:
        return False
    if expiry and as_of_date > expiry:
        return False
    return True


def select_unl_snapshot_contracts(
    all_contracts: list[Any],
    *,
    as_of_date: str | None = None,
    exclude_programme_id: str | None = None,
) -> list[Any]:
    """Select our own assumed UNL programmes in force as of `as_of_date`.

    - Excludes hedges (no circular self-hedging) and ILW/index deals (UNL book only).
    - When `as_of_date` is provided (ISO YYYY-MM-DD), selects programmes whose inception/expiry
      window covers that date (allowing BOUND, BOUND_FOR_OPS, and historical EXPIRED deals
      that were in force at that past date).
    - If `as_of_date` is omitted/empty, returns all currently BOUND assumed UNL programmes.
    """
    candidates = [
        c
        for c in all_contracts
        if c.programme_id != exclude_programme_id
        and not is_hedge_direction(c.direction)
        and not is_ilw_programme(c.structure, c.instrument)
    ]

    clean_date = (as_of_date or "").strip()
    if clean_date:
        active_statuses = {ProgrammeStatus.BOUND, ProgrammeStatus.BOUND_FOR_OPS, ProgrammeStatus.EXPIRED}
        return [
            c
            for c in candidates
            if c.status in active_statuses
            and _in_force_on(getattr(c, "inception_date", None), getattr(c, "expiry_date", None), clean_date)
        ]

    return [c for c in candidates if c.status in (ProgrammeStatus.BOUND, ProgrammeStatus.BOUND_FOR_OPS)]


def snapshot_loss_files(
    contracts: list[Any],
    *,
    as_of_date: str | None = None,
    fallback_sim_set_id: str = "sim-synth-cat-v1-100k",
) -> list[dict[str, Any]]:
    """Build loss file metadata covering every region-peril present in our UNL portfolio snapshot."""
    rps: list[str] = []
    for c in contracts:
        for rp in getattr(c, "region_perils", []) or []:
            if rp and rp not in rps:
                rps.append(rp)
    if not rps:
        if (as_of_date or "").strip():
            return []
        rps = list(REGION_PERILS)
    label = f"UNL Portfolio Snapshot ({as_of_date})" if as_of_date else "UNL Portfolio Snapshot"
    sim_id = f"unl-snapshot-{as_of_date}" if as_of_date else fallback_sim_set_id
    return files_from_keys(rps, sim_set_id=sim_id, name=label, vendor="UNL Portfolio")


def _rolled_up_programme_events(
    prog: Any,
    ev_map: dict[str, np.ndarray],
    all_deals: dict[str, Any],
) -> np.ndarray | None:
    """Aggregate a single assumed programme's event-level losses using the same selective-rollup rules as pricing_link."""
    total: np.ndarray | None = None
    nodes_by_id = {n.node_id: n for n in prog.graph.nodes}

    if graph_uses_explicit_pricing_links(prog.graph):
        claimed: set[tuple[str, str]] = set()
        for px in prog.graph.nodes:
            if px.node_type != NodeType.PRICING_OUTPUT:
                continue
            target_cid = str(px.params.get("contractId") or "").strip()
            if not target_cid:
                continue
            target_prog = all_deals.get(target_cid)
            if target_prog is None:
                continue
            target_layers = [n for n in target_prog.graph.nodes if n.node_type in LAYER_NODE_TYPES]
            if not target_layers:
                continue
            wanted_lid = str(px.params.get("layerNodeId") or "").strip()
            target_layer = next((n for n in target_layers if n.node_id == wanted_lid), target_layers[0])
            key = (target_prog.programme_id, target_layer.node_id)
            if key in claimed:
                continue
            claimed.add(key)
            target_signed, _ = _layer_share_and_premium(target_layer, target_prog.signed_line)
            in_edges = [e for e in prog.graph.edges if e.target == px.node_id and e.source in nodes_by_id]
            if not in_edges:
                continue
            raw_ev = ev_map[px.node_id]
            upstream_share = 1.0
            layer_inputs = [e for e in in_edges if nodes_by_id[e.source].node_type in LAYER_NODE_TYPES]
            if len(in_edges) == 1 and layer_inputs:
                e = layer_inputs[0]
                share, _ = _layer_share_and_premium(nodes_by_id[e.source], prog.signed_line)
                upstream_share = share * e.sign * e.weight
            scale = (target_signed / upstream_share) if upstream_share > 1e-12 else 0.0
            contrib = raw_ev * scale
            total = contrib.copy() if total is None else (total + contrib)
    else:
        for node in prog.graph.nodes:
            if node.node_type not in ROLLUP_TERM_TYPES:
                continue
            contrib = ev_map[node.node_id]
            total = contrib.copy() if total is None else (total + contrib)
    return total


def compute_unl_portfolio_events(
    deals: list[Any],
    yelt: YeltTable,
    view: ViewOfRisk,
    *,
    all_deals: dict[str, Any] | None = None,
    as_of_date: str | None = None,
) -> tuple[np.ndarray, str]:
    """Compute event-level UNL portfolio loss vector across the given assumed UNL deals."""
    from app.engine.slice_runner import evaluate_programme_dag

    catalog = dict(all_deals or {})
    for d in deals:
        catalog[d.programme_id] = d

    currencies = {getattr(d, "currency", yelt.currency) for d in deals}
    if len(currencies) > 1 or (currencies and yelt.currency not in currencies):
        raise ValueError(
            f"Cannot mix currencies without FX_CONVERSION in UNL portfolio snapshot: {sorted(currencies | {yelt.currency})}"
        )

    total = np.zeros_like(yelt.losses, dtype=np.float64)
    sig_parts: list[dict[str, Any]] = []
    for prog in sorted(deals, key=lambda p: p.programme_id):
        _, vecs = evaluate_programme_dag(prog, yelt, LineageRegistry(persist=False), views=[view])
        ev_map = vecs["events"][view.value]
        contrib = _rolled_up_programme_events(prog, ev_map, catalog)
        if contrib is not None:
            total += contrib
        sig_parts.append({
            "id": prog.programme_id,
            "graph": prog.graph.content_hash,
            "uplift": prog.uplift_set.content_hash if view == ViewOfRisk.ADJUSTED else "UNADJ",
            "signed": prog.signed_line,
        })

    snap_hash = canonical_hash({
        "as_of_date": as_of_date or "",
        "view": view.value,
        "sim_set": yelt.content_hash,
        "deals": sig_parts,
    })
    return total, snap_hash
