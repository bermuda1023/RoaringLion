"""Persistent Contract Management System (CMS) Ledger & One-Click Sliced Rollup Service."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, or_

from app.db.database import get_session
from app.db.models import ContractNoteRecord, ContractRecord, SimSetRecord
from app.models.domain import (
    GraphEdgeSpec,
    GraphNodeSpec,
    GraphSpec,
    ProgrammeDeal,
    UpliftRule,
    UpliftSet,
)
from app.models.enums import (
    ContractDirection,
    IlwTriggerType,
    InstrumentType,
    LinkType,
    NodeType,
    PerilCode,
    PlacementType,
    ProgrammeStatus,
    StructureType,
    is_hedge_direction,
)
from app.models.phase1 import (
    ContractCreate,
    ContractFull,
    ContractLayer,
    ContractLayerSpec,
    ContractLayerUpdate,
    ContractNoteCreate,
    ContractNoteOut,
    ContractUpdate,
    IlwPriceRequest,
    ProgrammeProfile,
    PricingBinding,
    RelatedProgramme,
    RenewalChainRow,
    RenewProgrammeRequest,
    SliceRollupRequest,
)
from app.services.lineage import LineageRegistry, canonical_hash
from app.services.node_labels import label_name, node_terms, relabel_graph, smart_label
from app.services.loss_files import files_from_stored, validate_filters_match_loss_files
from app.services.portfolio_snapshot import (
    is_ilw_programme,
    is_unl_retro_hedge,
    select_unl_snapshot_contracts,
    snapshot_loss_files,
)
from app.services.pricing_bind import validate_pricing_output_single_connection
from app.services.seed_data import _build_eurowind_programme, _build_farmers_programme, _build_pacific_eq_programme

DEFAULT_INDUSTRY_SIM_SET_ID = "ind-synth-cat-v1-100k"
_SOURCE_NODE_TYPES = (NodeType.LOSS_SOURCE, NodeType.INDUSTRY_INDEX_SOURCE, NodeType.PORTFOLIO_ROLLUP)


_OUTPUT_TYPES = (NodeType.PRICING_OUTPUT, NodeType.YELT_OUTPUT, NodeType.EP_OUTPUT)
_AUTO_LAYER_CODE_RE = re.compile(r"^L(\d+)$", re.IGNORECASE)


def _next_layer_code(used_lower: set[str]) -> str:
    nums = [int(m.group(1)) for code in used_lower if (m := _AUTO_LAYER_CODE_RE.match(code))]
    n = max(nums, default=0) + 1
    while f"l{n}" in used_lower:
        n += 1
    return f"L{n}"


def ensure_layer_codes(graph: GraphSpec, *, strict: bool = True) -> GraphSpec:
    """Ensure every financial-terms layer node on a programme graph has a unique non-empty layerCode."""
    layer_nodes = [n for n in graph.nodes if n.node_type in _LAYER_TYPES]
    used_lower: set[str] = set()
    missing: list[GraphNodeSpec] = []
    for node in layer_nodes:
        raw = node.params.get("layerCode")
        code = str(raw).strip() if raw is not None else ""
        if code:
            key = code.lower()
            if key in used_lower:
                if strict:
                    raise ValueError(f"Duplicate layer identifier '{code}' within this programme")
                missing.append(node)
                continue
            used_lower.add(key)
            node.params = {**node.params, "layerCode": code}
        else:
            missing.append(node)
    for node in missing:
        code = _next_layer_code(used_lower)
        used_lower.add(code.lower())
        node.params = {**node.params, "layerCode": code}
    return graph


def _layer_node(spec: ContractLayerSpec, node_id: str, written: float, signed: float, position: dict) -> GraphNodeSpec:
    signed_line = spec.signed_line if spec.signed_line is not None else signed
    if spec.written_line is not None:
        written_line = spec.written_line
    elif spec.signed_line is not None:
        written_line = spec.signed_line
    else:
        written_line = max(written, signed_line)
    if spec.layer_type == "AGG_XL":
        params: dict = {
            "aggLimit": spec.limit,
            "aggAttachment": spec.attachment,
            "perEventFranchise": 0.0,
            "depositPremium": spec.deposit_premium,
            "placedShare": signed_line,
        }
        if spec.per_event_cap is not None:
            params["perEventCap"] = spec.per_event_cap
        node_type, default_name = NodeType.AGG_XL, "Agg XL"
    elif spec.layer_type == "ILW_OPTION":
        params = {
            "limit": spec.limit,
            "trigger": spec.attachment,
            "triggerType": spec.trigger_type.value if spec.trigger_type else IlwTriggerType.BINARY.value,
            "depositPremium": spec.deposit_premium,
            "placedShare": signed_line,
        }
        if spec.exhaustion is not None:
            params["exhaustion"] = spec.exhaustion
        node_type, default_name = NodeType.ILW_OPTION, "ILW"
    else:
        params = {
            "occLimit": spec.limit,
            "occAttachment": spec.attachment,
            "occFranchise": 0.0,
            "reinstatements": spec.reinstatements,
            "ripRate": spec.rip_rate,
            "depositPremium": spec.deposit_premium,
            "placedShare": signed_line,
        }
        node_type, default_name = NodeType.OCC_XL, "Cat XL"
    if spec.rol is not None:
        params["depositPremium"] = round(spec.rol * spec.limit, 6)
    params["writtenLine"] = written_line
    if spec.brokerage_pct is not None:
        params["brokeragePct"] = spec.brokerage_pct
    if spec.rarc_pct is not None:
        params["rarcPct"] = spec.rarc_pct
    if spec.layer_code is not None and spec.layer_code.strip():
        params["layerCode"] = spec.layer_code.strip()
    params["labelName"] = (spec.name or "").strip() or default_name
    return GraphNodeSpec(
        node_id=node_id,
        node_type=node_type,
        label=smart_label(node_type, params, params["labelName"]),
        params=params,
        position=position,
    )


def _pricing_node(programme_id: str, layer_id: str, layer_name: str, position: dict) -> GraphNodeSpec:
    params: dict = {"contractId": programme_id, "layerNodeId": layer_id} if programme_id else {}
    return GraphNodeSpec(
        node_id=f"px-{layer_id}",
        node_type=NodeType.PRICING_OUTPUT,
        label=f"Pricing · {layer_name}",
        params=params,
        position=position,
    )


def _ordinal_name(i: int, layer_type: str) -> str:
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(i if i < 20 else i % 10, "th")
    kind = "ILW" if layer_type == "ILW_OPTION" else ("Agg XL" if layer_type == "AGG_XL" else "Cat XL")
    return f"{i}{suffix} {kind}"


def _contract_layer_specs(c: ContractCreate) -> list[ContractLayerSpec]:
    if c.layers:
        return [
            spec if (spec.name or "").strip() else spec.model_copy(update={"name": _ordinal_name(i + 1, spec.layer_type)})
            for i, spec in enumerate(c.layers)
        ]
    if is_ilw_programme(c.structure, c.instrument):
        ilw_spec = c.ilw_spec or {}
        raw_trig_type = ilw_spec.get("trigger_type") or ilw_spec.get("triggerType")
        try:
            trig_type = IlwTriggerType(str(raw_trig_type)) if raw_trig_type else IlwTriggerType.BINARY
        except ValueError:
            trig_type = IlwTriggerType.BINARY
        exh = ilw_spec.get("exhaustion_bn") or ilw_spec.get("exhaustionBn")
        return [
            ContractLayerSpec(
                name=c.programme_name,
                layer_type="ILW_OPTION",
                limit=c.limit,
                attachment=c.attachment,
                deposit_premium=c.deposit_premium,
                trigger_type=trig_type,
                exhaustion=float(exh) if exh is not None else None,
                written_line=c.written_line,
                signed_line=c.signed_line,
            )
        ]
    return [
        ContractLayerSpec(
            name=c.programme_name,
            layer_type="AGG_XL" if c.structure == StructureType.AGG_XL else "OCC_XL",
            limit=c.limit,
            attachment=c.attachment,
            deposit_premium=c.deposit_premium,
            written_line=c.written_line,
            signed_line=c.signed_line,
        )
    ]


def _sim_loss_files(db) -> dict[str, list]:
    out: dict[str, list] = {}
    for rec in db.query(SimSetRecord).all():
        out[rec.sim_set_id] = files_from_stored(
            getattr(rec, "loss_files_json", "[]"),
            json.loads(rec.region_perils_json or "[]"),
            sim_set_id=rec.sim_set_id,
            name=rec.name,
        )
    return out


def _loss_files_for_sim(sim_set_id: str) -> list:
    with get_session() as db:
        rec = db.get(SimSetRecord, sim_set_id)
        if rec is None:
            return []
        return files_from_stored(
            getattr(rec, "loss_files_json", "[]"),
            json.loads(rec.region_perils_json or "[]"),
            sim_set_id=rec.sim_set_id,
            name=rec.name,
        )


def _build_source_node(
    *,
    src_id: str,
    direction: ContractDirection,
    structure: StructureType,
    instrument: InstrumentType,
    sim_set_id: str,
    portfolio_snapshot_date: str | None,
    inception_date: str | None,
    all_contracts: list[ContractFull] | None = None,
    programme_id: str = "",
    position: dict[str, float] | None = None,
) -> GraphNodeSpec:
    pos = position or {"x": 40, "y": 100}
    if is_ilw_programme(structure, instrument):
        ind_sim_id = sim_set_id if sim_set_id.startswith("ind-") else DEFAULT_INDUSTRY_SIM_SET_ID
        loss_files = _loss_files_for_sim(ind_sim_id)
        params: dict = {
            "simSetId": ind_sim_id,
            "sourceKind": "INDUSTRY_LOSS_RUN",
            "labelName": "Industry Loss Run",
        }
        if loss_files:
            params["lossFiles"] = loss_files
        return GraphNodeSpec(
            node_id=src_id,
            node_type=NodeType.INDUSTRY_INDEX_SOURCE,
            label=smart_label(NodeType.INDUSTRY_INDEX_SOURCE, params, "Industry Loss Run"),
            params=params,
            position=pos,
        )
    if is_unl_retro_hedge(direction, structure, instrument):
        as_of = (portfolio_snapshot_date or inception_date or "2027-01-01").strip()
        snap_contracts = select_unl_snapshot_contracts(
            all_contracts or [],
            as_of_date=as_of,
            exclude_programme_id=programme_id or None,
        )
        loss_files = snapshot_loss_files(snap_contracts, as_of_date=as_of, fallback_sim_set_id=sim_set_id)
        params = {
            "simSetId": sim_set_id,
            "sourceKind": "UNL_PORTFOLIO_SNAPSHOT",
            "asOfDate": as_of,
            "labelName": "UNL Portfolio",
            "lossFiles": loss_files,
        }
        return GraphNodeSpec(
            node_id=src_id,
            node_type=NodeType.PORTFOLIO_ROLLUP,
            label=smart_label(NodeType.PORTFOLIO_ROLLUP, params, "UNL Portfolio"),
            params=params,
            position=pos,
        )
    source_params: dict = {"simSetId": sim_set_id}
    loss_files = _loss_files_for_sim(sim_set_id)
    if loss_files:
        source_params["lossFiles"] = loss_files
    return GraphNodeSpec(
        node_id=src_id,
        node_type=NodeType.LOSS_SOURCE,
        label=f"Loss Source ({sim_set_id})",
        params=source_params,
        position=pos,
    )


def _auto_wire_graph_source(
    graph: GraphSpec,
    *,
    direction: ContractDirection,
    structure: StructureType,
    instrument: InstrumentType,
    sim_set_id: str,
    portfolio_snapshot_date: str | None,
    inception_date: str | None,
    all_contracts: list[ContractFull] | None = None,
    programme_id: str = "",
) -> bool:
    """Re-wire the graph's primary loss source when direction/structure/snapshot date changes."""
    changed = False
    for idx, node in enumerate(graph.nodes):
        if node.node_type not in _SOURCE_NODE_TYPES:
            continue
        replacement = _build_source_node(
            src_id=node.node_id,
            direction=direction,
            structure=structure,
            instrument=instrument,
            sim_set_id=sim_set_id,
            portfolio_snapshot_date=portfolio_snapshot_date,
            inception_date=inception_date,
            all_contracts=all_contracts,
            programme_id=programme_id,
            position=node.position,
        )
        if node.node_type != replacement.node_type or node.params != replacement.params:
            graph.nodes[idx] = replacement
            changed = True
        break
    return changed


def _default_graph_for_contract(
    c: ContractCreate,
    prog_id: str,
    *,
    all_contracts: list[ContractFull] | None = None,
) -> GraphSpec:
    """Loss source -> zone/peril filter -> one node per layer, each priced, with a programme-total YELT.

    ILW programmes auto-wire to the Industry Loss Run (`INDUSTRY_INDEX_SOURCE`) with all industry
    regions & perils available on the filter out of the box.
    Normal retro hedges auto-wire to our own UNL Portfolio Snapshot (`PORTFOLIO_ROLLUP`) as of
    `portfolio_snapshot_date`.
    """
    src_id, flt_id, out_id = f"src-{prog_id}", f"flt-{prog_id}", f"out-{prog_id}"
    src_node = _build_source_node(
        src_id=src_id,
        direction=c.direction,
        structure=c.structure,
        instrument=c.instrument,
        sim_set_id=c.sim_set_id,
        portfolio_snapshot_date=c.portfolio_snapshot_date,
        inception_date=c.inception_date,
        all_contracts=all_contracts,
        programme_id=prog_id,
        position={"x": 40, "y": 100},
    )
    flt_rps = [rp for rp in c.region_perils if rp and rp != "ALL"]
    nodes = [
        src_node,
        GraphNodeSpec(
            node_id=flt_id,
            node_type=NodeType.REGION_PERIL_FILTER,
            label=f"Zone Peril ({', '.join(flt_rps)})" if flt_rps else "Zone Peril — All zones & perils",
            params={"regionPerils": flt_rps, "labelName": "Zone Peril"},
            position={"x": 300, "y": 100},
        ),
    ]
    edges = [GraphEdgeSpec(edge_id=f"e1-{prog_id}", source=src_id, target=flt_id, sign=1, weight=1.0)]
    specs = _contract_layer_specs(c)
    for i, spec in enumerate(specs):
        lyr_id = f"lyr-{prog_id}" if i == 0 else f"lyr-{prog_id}-{i + 1}"
        y = 20 + i * 140
        layer = _layer_node(spec, lyr_id, c.written_line, c.signed_line, {"x": 600, "y": y})
        nodes.append(layer)
        nodes.append(_pricing_node(prog_id, lyr_id, layer.params["labelName"], {"x": 920, "y": y}))
        edges += [
            GraphEdgeSpec(edge_id=f"e-in-{lyr_id}", source=flt_id, target=lyr_id, sign=1, weight=1.0),
            GraphEdgeSpec(edge_id=f"e-px-{lyr_id}", source=lyr_id, target=f"px-{lyr_id}", sign=1, weight=1.0),
            GraphEdgeSpec(edge_id=f"e-out-{lyr_id}", source=lyr_id, target=out_id, sign=1, weight=1.0),
        ]
    nodes.append(
        GraphNodeSpec(
            node_id=out_id,
            node_type=NodeType.YELT_OUTPUT,
            label="Programme YELT",
            params={"view": "ADJUSTED"},
            position={"x": 1220, "y": 20 + (len(specs) - 1) * 70},
        )
    )
    graph = GraphSpec(
        graph_id=f"grp-{prog_id}",
        name=f"{c.cedent_name} — {c.programme_name}",
        version=1,
        currency=c.currency,
        nodes=nodes,
        edges=edges,
        content_hash="",
    )
    ensure_layer_codes(graph, strict=True)
    relabel_graph(graph)
    graph.content_hash = canonical_hash({"nodes": [n.model_dump() for n in nodes], "edges": [e.model_dump() for e in edges]})
    return graph


def add_layer_to_graph(
    graph: GraphSpec,
    spec: ContractLayerSpec,
    written: float,
    signed: float,
    *,
    programme_id: str = "",
) -> str:
    """Add a layer wired like its siblings (same inputs), with its own Pricing Output. A YELT fed by
    every existing layer is the programme total, so the new layer joins it too."""
    ensure_layer_codes(graph, strict=False)
    layers = [n for n in graph.nodes if n.node_type in _LAYER_TYPES]
    layer_ids = {n.node_id for n in layers}
    used_codes = {str(n.params.get("layerCode") or "").strip().lower() for n in layers}
    if spec.layer_code is not None and spec.layer_code.strip():
        code = spec.layer_code.strip()
        if code.lower() in used_codes:
            raise ValueError(f"Layer identifier '{code}' already exists on this programme")
    else:
        code = _next_layer_code(used_codes)
    if layers:
        inputs = [(e.source, e.sign, e.weight) for e in graph.edges if e.target == layers[0].node_id]
    else:
        anchor = next(
            (
                n
                for t in (
                    NodeType.REGION_PERIL_FILTER,
                    NodeType.LOSS_SOURCE,
                    NodeType.INDUSTRY_INDEX_SOURCE,
                    NodeType.PORTFOLIO_ROLLUP,
                )
                for n in graph.nodes
                if n.node_type == t
            ),
            None,
        )
        if anchor is None:
            raise ValueError("Add a loss source to the programme's structure before adding layers")
        inputs = [(anchor.node_id, 1, 1.0)]
    lyr_id = f"lyr-{uuid.uuid4().hex[:8]}"
    x = layers[0].position.get("x", 600) if layers else 600
    y = max((n.position.get("y", 0) for n in layers), default=-120) + 140
    name = spec.name if (spec.name or "").strip() else _ordinal_name(len(layers) + 1, spec.layer_type)
    layer = _layer_node(spec.model_copy(update={"name": name, "layer_code": code}), lyr_id, written, signed, {"x": x, "y": y})
    graph.nodes.append(layer)
    graph.nodes.append(_pricing_node(programme_id, lyr_id, layer.params["labelName"], {"x": x + 320, "y": y}))
    for src, sign, weight in inputs:
        graph.edges.append(GraphEdgeSpec(edge_id=f"e-{uuid.uuid4().hex[:8]}", source=src, target=lyr_id, sign=sign, weight=weight))
    graph.edges.append(GraphEdgeSpec(edge_id=f"e-px-{lyr_id}", source=lyr_id, target=f"px-{lyr_id}", sign=1, weight=1.0))
    if layer_ids:
        for out in graph.nodes:
            if out.node_type != NodeType.YELT_OUTPUT:
                continue
            fed_by = {e.source for e in graph.edges if e.target == out.node_id}
            if fed_by and fed_by == layer_ids:
                graph.edges.append(GraphEdgeSpec(edge_id=f"e-{uuid.uuid4().hex[:8]}", source=lyr_id, target=out.node_id, sign=1, weight=1.0))
    return lyr_id


def remove_layer_from_graph(graph: GraphSpec, node_id: str, *, programme_id: str = "") -> None:
    """Remove a layer and any output nodes left with nothing feeding them."""
    node = next((n for n in graph.nodes if n.node_id == node_id), None)
    if node is None or node.node_type not in _LAYER_TYPES:
        raise KeyError(f"Layer {node_id} not found on this programme")
    if sum(1 for n in graph.nodes if n.node_type in _LAYER_TYPES) <= 1:
        raise ValueError("A programme needs at least one layer")
    fed = {e.target for e in graph.edges if e.source == node_id}
    graph.nodes = [n for n in graph.nodes if n.node_id != node_id]
    graph.edges = [e for e in graph.edges if node_id not in (e.source, e.target)]
    still_fed = {e.target for e in graph.edges}
    orphans = {n.node_id for n in graph.nodes if n.node_id in fed and n.node_type in _OUTPUT_TYPES and n.node_id not in still_fed}
    graph.nodes = [n for n in graph.nodes if n.node_id not in orphans]
    graph.edges = [e for e in graph.edges if e.source not in orphans and e.target not in orphans]
    for n in graph.nodes:
        if n.node_type == NodeType.PRICING_OUTPUT and n.params.get("layerNodeId") == node_id:
            if not programme_id or n.params.get("contractId") == programme_id:
                p = {k: v for k, v in n.params.items() if k not in ("contractId", "layerNodeId", "linkedLabel")}
                p["unlinked"] = True
                n.params = p


def _record_to_full(r: ContractRecord) -> ContractFull:
    direction = ContractDirection(r.direction)
    return ContractFull(
        programme_id=r.programme_id,
        chain_id=r.chain_id,
        prior_contract_id=r.prior_contract_id,
        cedent_name=r.cedent_name,
        programme_name=r.programme_name,
        treaty_year=r.treaty_year,
        renewal_period=r.renewal_period,
        office=r.office,
        underwriter=r.underwriter,
        secondary_underwriter=r.secondary_underwriter,
        broker=r.broker,
        fronting_carrier=r.fronting_carrier,
        collateral_cell=r.collateral_cell,
        status=ProgrammeStatus(r.status),
        direction=direction,
        is_hedge=is_hedge_direction(direction),
        structure=StructureType(r.structure),
        instrument=InstrumentType(r.instrument),
        placement=PlacementType(r.placement),
        inception_date=r.inception_date,
        expiry_date=r.expiry_date,
        portfolio_snapshot_date=getattr(r, "portfolio_snapshot_date", None),
        hours_clause=r.hours_clause,
        written_line=r.written_line if r.written_line is not None else 1.0,
        signed_line=r.signed_line,
        limit=r.limit,
        attachment=r.attachment,
        deposit_premium=r.deposit_premium,
        rol=r.rol,
        brokerage_pct=r.brokerage_pct,
        fronting_fee_pct=r.fronting_fee_pct,
        collateral_required=r.collateral_required,
        currency=r.currency,
        sim_set_id=r.sim_set_id,
        exposure_eclipse_programme_id=r.exposure_eclipse_programme_id,
        region_perils=json.loads(r.region_perils_json),
        perils=[PerilCode(p) for p in json.loads(r.perils_json)],
        custom_tags=json.loads(r.custom_tags_json),
        uplift_set=UpliftSet(**json.loads(r.uplift_set_json)),
        graph=GraphSpec(**json.loads(r.graph_json)),
        ilw_spec=json.loads(r.ilw_spec_json) if r.ilw_spec_json else None,
        profile=ProgrammeProfile(**json.loads(r.profile_json)) if r.profile_json else ProgrammeProfile(),
        version=r.version,
        content_hash=r.content_hash,
        updated_at=r.updated_at,
    )


def contract_to_programme_deal(c: ContractFull) -> ProgrammeDeal:
    return ProgrammeDeal(
        programme_id=c.programme_id,
        cedent_name=c.cedent_name,
        programme_name=c.programme_name,
        treaty_year=c.treaty_year,
        renewal_period=c.renewal_period,
        office=c.office,
        underwriter=c.underwriter,
        broker=c.broker,
        status=c.status,
        direction=c.direction,
        is_hedge=is_hedge_direction(c.direction),
        structure=c.structure,
        instrument=c.instrument,
        inception_date=c.inception_date,
        expiry_date=c.expiry_date,
        portfolio_snapshot_date=c.portfolio_snapshot_date,
        currency=c.currency,
        sim_set_id=c.sim_set_id,
        uplift_set=c.uplift_set,
        graph=c.graph,
        written_line=c.written_line,
        signed_line=c.signed_line,
        deposit_premium=c.deposit_premium,
        limit=c.limit,
        rol=c.rol,
        collateral_required=c.collateral_required,
        region_perils=c.region_perils,
        perils=c.perils,
    )



_LAYER_TYPES = {
    NodeType.OCC_XL: ("occLimit", "occAttachment"),
    NodeType.AGG_XL: ("aggLimit", "aggAttachment"),
    NodeType.ILW_OPTION: ("limit", "trigger"),
    NodeType.QUOTA_SHARE: ("aggCap", None),
    NodeType.AGG_QUOTA_SHARE: ("aggCap", None),
    NodeType.SURPLUS_SHARE: (None, None),
    NodeType.CASCADING_TOWER: (None, None),
}


def _opt_float(v) -> float | None:
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    return float(v)


def compute_weighted_rarc(layers: list[ContractLayer]) -> float | None:
    """Line-size-weighted average RARC (in percentage points) across layers with RARC set.

    Weighting hierarchy:
    1. Signed line size: ``limit * signed_line`` (our placed exposure in $M).
    2. Written line size: ``limit * written_line`` when total signed line size across RARC layers is 0.
    3. 100% layer limit: ``limit`` when both signed and written line sizes are 0.
    """
    with_rarc = [l for l in layers if l.rarc_pct is not None and (l.limit or 0.0) > 0]
    if not with_rarc:
        return None
    for weight_fn in (
        lambda l: (l.limit or 0.0) * l.signed_line,
        lambda l: (l.limit or 0.0) * l.written_line,
        lambda l: (l.limit or 0.0),
    ):
        total_w = sum(weight_fn(l) for l in with_rarc)
        if total_w > 0:
            return round(sum(weight_fn(l) * (l.rarc_pct or 0.0) for l in with_rarc) / total_w, 4)
    return None


def enrich_layers_with_loss_ratio(
    layers: list[ContractLayer],
    adjusted_nodes: list,
    programme_id: str,
) -> list[ContractLayer]:
    """Attach read-only modelled Adjusted EL, Expected RIP, and Loss Ratio to each ContractLayer."""
    pricing_by_layer = {
        m.pricing.layer_node_id: m.pricing
        for m in adjusted_nodes
        if getattr(m, "pricing", None) is not None and m.pricing.contract_id == programme_id
    }
    node_by_id = {m.node_id: m for m in adjusted_nodes}
    for l in layers:
        p = pricing_by_layer.get(l.node_id)
        if p is not None:
            l.expected_loss = round(float(p.expected_loss), 6)
            l.expected_rip = round(float(p.expected_rip), 6)
            l.loss_ratio = p.loss_ratio
            continue
        m = node_by_id.get(l.node_id)
        if m is not None:
            l.expected_loss = round(float(m.el), 6)
            l.expected_rip = round(float(m.expected_rip), 6)
            if m.elr is not None:
                l.loss_ratio = m.elr
            elif l.our_premium > 0:
                income = l.our_premium + (l.expected_rip or 0.0)
                l.loss_ratio = round(l.expected_loss / income, 6) if income > 0 else None
            else:
                l.loss_ratio = None
    return layers


def compute_programme_loss_ratio(
    layers: list[ContractLayer],
    adjusted_nodes: list,
    programme_id: str,
) -> float | None:
    """Programme-level Loss Ratio = sum(Adjusted EL) / sum(Our Deposit Premium + Expected RIP)."""
    enriched = enrich_layers_with_loss_ratio(
        [l.model_copy() for l in layers],
        adjusted_nodes,
        programme_id,
    )
    linked = [l for l in enriched if l.linked_pricing_node_id]
    active = linked if linked else enriched
    total_el = 0.0
    total_income = 0.0
    for l in active:
        if l.expected_loss is None or l.our_premium <= 0:
            continue
        total_el += l.expected_loss
        total_income += l.our_premium + (l.expected_rip or 0.0)
    return round(total_el / total_income, 6) if total_income > 0 else None


def graph_layers(
    graph: GraphSpec,
    default_written: float,
    default_signed: float,
    *,
    default_brokerage: float = 0.10,
    programme_id: str | None = None,
    pricing_link_map: dict[str, tuple[str, str, str | None, str | None]] | None = None,
) -> list[ContractLayer]:
    """Financial-terms nodes of a programme graph presented as contract layers (limits at 100%).
    Each layer carries its own price, lines, brokerage and RARC; contract values are defaults."""
    out: list[ContractLayer] = []
    used_codes: set[str] = set()
    assigned_codes: dict[str, str] = {}
    for node in graph.nodes:
        if node.node_type not in _LAYER_TYPES:
            continue
        raw = node.params.get("layerCode")
        code = str(raw).strip() if raw is not None else ""
        if code and code.lower() not in used_codes:
            used_codes.add(code.lower())
            assigned_codes[node.node_id] = code
    for node in graph.nodes:
        if node.node_type not in _LAYER_TYPES or node.node_id in assigned_codes:
            continue
        code = _next_layer_code(used_codes)
        used_codes.add(code.lower())
        assigned_codes[node.node_id] = code

    link_by_layer: dict[str, tuple[str, str, str | None, str | None]] = dict(pricing_link_map or {})
    if programme_id:
        nodes_by_id = {n.node_id: n for n in graph.nodes}
        for px in graph.nodes:
            if px.node_type != NodeType.PRICING_OUTPUT:
                continue
            cid = str(px.params.get("contractId") or "").strip()
            lid = str(px.params.get("layerNodeId") or "").strip()
            if cid == programme_id and lid and lid not in link_by_layer:
                in_srcs = [nodes_by_id[e.source] for e in graph.edges if e.target == px.node_id and e.source in nodes_by_id]
                feeder_id = in_srcs[0].node_id if in_srcs else None
                feeder_label = " + ".join(s.label for s in in_srcs) if in_srcs else None
                link_by_layer[lid] = (px.node_id, px.label, feeder_id, feeder_label)

    for node in graph.nodes:
        if node.node_type not in _LAYER_TYPES:
            continue
        p = node.params
        limit_key, attach_key = _LAYER_TYPES[node.node_type]
        editable = ["layerCode", "label", "depositPremium", "writtenLine", "signedLine", "brokeragePct", "rarcPct"]
        if node.node_type == NodeType.CASCADING_TOWER:
            subs = p.get("layers", []) or []
            limit = sum(_opt_float(l.get("occLimit")) or 0.0 for l in subs) or None
            att_vals = [_opt_float(l.get("occAttachment")) for l in subs]
            attachment = min((a for a in att_vals if a is not None), default=None)
            premium = sum(_opt_float(l.get("depositPremium")) or 0.0 for l in subs)
            sub_signed = _opt_float(subs[0].get("placedShare")) if subs else None
            signed = default_signed if sub_signed is None else sub_signed
            editable = [f for f in editable if f != "depositPremium"]
        else:
            limit = _opt_float(p.get(limit_key)) if limit_key else None
            attachment = _opt_float(p.get(attach_key)) if attach_key else None
            premium = _opt_float(p.get("depositPremium")) or 0.0
            signed_val = _opt_float(p.get("placedShare"))
            signed = default_signed if signed_val is None else signed_val
            if limit_key:
                editable += ["limit", "rol"]
            if attach_key:
                editable.append("attachment")
            if node.node_type == NodeType.OCC_XL:
                editable += ["reinstatements", "ripRate"]
        raw_written = _opt_float(p.get("writtenLine"))
        if raw_written is None:
            raw_written = _opt_float(p.get("authorizedLine"))
        written = raw_written if raw_written is not None else max(default_written, signed)
        brokerage_val = _opt_float(p.get("brokeragePct"))
        brokerage = default_brokerage if brokerage_val is None else brokerage_val
        rarc_pct = _opt_float(p.get("rarcPct"))
        rol = premium / limit if limit else None
        our_premium = premium * signed
        link_info = link_by_layer.get(node.node_id)
        out.append(
            ContractLayer(
                node_id=node.node_id,
                layer_code=assigned_codes[node.node_id],
                label=node.label,
                node_type=node.node_type.value,
                limit=limit,
                attachment=attachment,
                deposit_premium=round(premium, 6),
                rol=round(rol, 6) if rol is not None else None,
                reinstatements=_opt_float(p.get("reinstatements")) if node.node_type == NodeType.OCC_XL else None,
                rip_rate=_opt_float(p.get("ripRate")) if node.node_type == NodeType.OCC_XL else None,
                written_line=written,
                signed_line=signed,
                our_limit=round(limit * signed, 6) if limit else None,
                our_premium=round(our_premium, 6),
                brokerage_pct=brokerage,
                our_net_premium=round(our_premium * (1 - brokerage), 6),
                rarc_pct=rarc_pct,
                editable=editable,
                name=label_name(p, node.label),
                terms=node_terms(node.node_type, p),
                auto_label=p.get("autoLabel") is not False,
                linked_pricing_node_id=link_info[0] if link_info else None,
                linked_pricing_label=link_info[1] if link_info else None,
                linked_feeder_node_id=link_info[2] if link_info else None,
                linked_feeder_label=link_info[3] if link_info else None,
            )
        )
    return out


def contract_layers(c: ContractFull, *, all_contracts: list[ContractFull] | None = None) -> list[ContractLayer]:
    link_map: dict[str, tuple[str, str, str | None, str | None]] = {}
    if all_contracts:
        for other in all_contracts:
            nodes_by_id = {n.node_id: n for n in other.graph.nodes}
            for px in other.graph.nodes:
                if px.node_type != NodeType.PRICING_OUTPUT:
                    continue
                cid = str(px.params.get("contractId") or "").strip()
                lid = str(px.params.get("layerNodeId") or "").strip()
                if cid == c.programme_id and lid and lid not in link_map:
                    in_srcs = [nodes_by_id[e.source] for e in other.graph.edges if e.target == px.node_id and e.source in nodes_by_id]
                    feeder_id = in_srcs[0].node_id if in_srcs else None
                    feeder_label = " + ".join(s.label for s in in_srcs) if in_srcs else None
                    link_map[lid] = (px.node_id, px.label, feeder_id, feeder_label)
    return graph_layers(
        c.graph,
        c.written_line,
        c.signed_line,
        default_brokerage=c.brokerage_pct,
        programme_id=c.programme_id,
        pricing_link_map=link_map,
    )


def validate_pricing_links_one_to_one(
    programme_id: str,
    graph: GraphSpec,
    all_contracts: list[ContractFull],
) -> None:
    """Enforce strict 1:1 mapping between Pricing Output nodes and CMS contract layers across all contracts."""
    contracts_by_id: dict[str, ContractFull] = {c.programme_id: c for c in all_contracts}
    if programme_id in contracts_by_id:
        contracts_by_id[programme_id] = contracts_by_id[programme_id].model_copy(update={"graph": graph})

    # Collect (target_contract_id, target_layer_node_id) already linked by OTHER saved contracts
    other_claims: dict[tuple[str, str], tuple[str, str]] = {}
    for other in all_contracts:
        if other.programme_id == programme_id:
            continue
        owner_code = other.profile.unique_code or other.programme_id
        for px in other.graph.nodes:
            if px.node_type != NodeType.PRICING_OUTPUT:
                continue
            cid = str(px.params.get("contractId") or "").strip()
            if not cid:
                continue
            target = contracts_by_id.get(cid)
            if target is None:
                continue
            t_layers = contract_layers(target)
            if not t_layers:
                continue
            wanted = str(px.params.get("layerNodeId") or "").strip()
            t_layer = next((l for l in t_layers if l.node_id == wanted), t_layers[0])
            other_claims[(cid, t_layer.node_id)] = (owner_code, px.label)

    seen_in_graph: dict[tuple[str, str], str] = {}
    for px in graph.nodes:
        if px.node_type != NodeType.PRICING_OUTPUT:
            continue
        cid = str(px.params.get("contractId") or "").strip()
        if not cid:
            continue
        target = contracts_by_id.get(cid)
        if target is None:
            raise ValueError(f"Pricing Output '{px.label}' links to unknown contract '{cid}'")
        t_layers = contract_layers(target)
        if not t_layers:
            raise ValueError(f"Contract '{target.profile.unique_code or cid}' has no CMS layers to link")
        wanted = str(px.params.get("layerNodeId") or "").strip()
        if wanted:
            t_layer = next((l for l in t_layers if l.node_id == wanted), None)
            if t_layer is None:
                raise ValueError(
                    f"Pricing Output '{px.label}' links to unknown layer '{wanted}' on contract '{target.profile.unique_code or cid}'"
                )
        else:
            t_layer = t_layers[0]
            px.params = {**px.params, "layerNodeId": t_layer.node_id}

        key = (cid, t_layer.node_id)
        layer_desc = f"{t_layer.layer_code} ({t_layer.name or t_layer.label})" if t_layer.layer_code else (t_layer.name or t_layer.label)
        target_code = target.profile.unique_code or cid
        if key in seen_in_graph:
            raise ValueError(
                f"CMS layer {layer_desc} on {target_code} is already linked to Pricing Output '{seen_in_graph[key]}'. "
                "Pricing Outputs must be 1:1 with contract layers."
            )
        if key in other_claims:
            owner_code, owner_label = other_claims[key]
            raise ValueError(
                f"CMS layer {layer_desc} on {target_code} is already linked to '{owner_label}' on {owner_code}. "
                "Unlink it there first — Pricing Outputs must be 1:1 with contract layers."
            )
        seen_in_graph[key] = px.label


def apply_layer_updates(graph: GraphSpec, updates: list[ContractLayerUpdate]) -> GraphSpec:
    ensure_layer_codes(graph, strict=False)
    nodes = {n.node_id: n for n in graph.nodes}
    for u in updates:
        node = nodes.get(u.node_id)
        if node is None or node.node_type not in _LAYER_TYPES:
            raise KeyError(f"Layer {u.node_id} not found on this programme")
        for name, val in (
            ("Written line", u.written_line),
            ("Signed line", u.signed_line),
        ):
            if val is not None and not 0.0 <= val <= 1.0:
                raise ValueError(f"{node.label}: {name} must be between 0% and 100%")
        if u.brokerage_pct is not None and not 0.0 <= u.brokerage_pct <= 0.9:
            raise ValueError(f"{node.label}: brokerage must be between 0% and 90%")
        if u.rol is not None and not 0.0 <= u.rol <= 5.0:
            raise ValueError(f"{node.label}: ROL must be between 0% and 500%")
        if u.rarc_pct is not None and not -100.0 <= u.rarc_pct <= 500.0:
            raise ValueError(f"{node.label}: RARC must be between -100% and 500%")
        p = dict(node.params)
        p.pop("authorizedLine", None)
        p.pop("technicalRol", None)
        if u.layer_code is not None:
            code = u.layer_code.strip()
            if not code:
                raise ValueError(f"{node.label}: layer identifier is required")
            p["layerCode"] = code
        limit_key, attach_key = _LAYER_TYPES[node.node_type]
        if u.label is not None:
            if p.get("autoLabel") is False:
                if u.label.strip():
                    node.label = u.label.strip()
            else:
                p["labelName"] = u.label.strip()
        if node.node_type == NodeType.CASCADING_TOWER:
            if u.signed_line is not None:
                p["layers"] = [{**l, "placedShare": u.signed_line} for l in p.get("layers", []) or []]
        else:
            if u.limit is not None and limit_key:
                if u.limit <= 0:
                    raise ValueError(f"{node.label}: limit must be greater than 0")
                p[limit_key] = u.limit
            if u.attachment is not None and attach_key:
                if u.attachment < 0:
                    raise ValueError(f"{node.label}: attachment must be 0 or more")
                p[attach_key] = u.attachment
            if u.deposit_premium is not None:
                if u.deposit_premium < 0:
                    raise ValueError(f"{node.label}: premium must be 0 or more")
                p["depositPremium"] = u.deposit_premium
            elif u.rol is not None and limit_key and p.get(limit_key):
                p["depositPremium"] = round(u.rol * float(p[limit_key]), 6)
            if u.reinstatements is not None and node.node_type == NodeType.OCC_XL:
                p["reinstatements"] = u.reinstatements
            if u.rip_rate is not None and node.node_type == NodeType.OCC_XL:
                p["ripRate"] = u.rip_rate
            if u.signed_line is not None:
                p["placedShare"] = u.signed_line
        if u.written_line is not None:
            p["writtenLine"] = u.written_line
        if u.brokerage_pct is not None:
            p["brokeragePct"] = u.brokerage_pct
        if "rarc_pct" in u.model_fields_set:
            if u.rarc_pct is None:
                p.pop("rarcPct", None)
            else:
                p["rarcPct"] = u.rarc_pct
        node.params = p
        node.label = smart_label(node.node_type, p, node.label)
    ensure_layer_codes(graph, strict=True)
    return graph


def _active_rollup_layers(rec: ContractRecord, graph: GraphSpec) -> list[ContractLayer]:
    all_layers = [
        l for l in graph_layers(
            graph,
            rec.written_line if rec.written_line is not None else 1.0,
            rec.signed_line,
            default_brokerage=rec.brokerage_pct,
            programme_id=rec.programme_id,
        )
        if l.limit
    ]
    linked_ids = {
        str(n.params.get("layerNodeId") or "").strip()
        for n in graph.nodes
        if n.node_type == NodeType.PRICING_OUTPUT
        and str(n.params.get("contractId") or "").strip() == rec.programme_id
        and str(n.params.get("layerNodeId") or "").strip()
    }
    return [l for l in all_layers if l.node_id in linked_ids] if linked_ids else all_layers


def _sync_profile_rarc_from_layers(
    rec: ContractRecord,
    layers: list[ContractLayer],
    *,
    clear_if_empty: bool = False,
) -> None:
    weighted_rarc = compute_weighted_rarc(layers)
    if weighted_rarc is None and not clear_if_empty:
        return
    prof = ProgrammeProfile(**json.loads(rec.profile_json)) if rec.profile_json else ProgrammeProfile()
    prof.rarc_total_pct = weighted_rarc if weighted_rarc is not None else 0.0
    rec.profile_json = prof.model_dump_json()


def _roll_up_layers(rec: ContractRecord, graph: GraphSpec, *, clear_rarc_if_empty: bool = False) -> None:
    """Programme totals follow the layers: summed 100% limit/premium, limit-weighted lines,
    premium-weighted brokerage, and line-size-weighted RARC. When the graph has explicit Pricing
    Output -> CMS links for this programme, only those linked CMS layers roll up into contract
    totals (unlinked exploratory canvas layers stay out of the booked totals)."""
    layers = _active_rollup_layers(rec, graph)
    total_limit = sum(l.limit or 0.0 for l in layers)
    if total_limit <= 0:
        return

    def by_limit(get) -> float:
        return round(sum((l.limit or 0) * get(l) for l in layers) / total_limit, 6)

    rec.limit = round(total_limit, 6)
    rec.deposit_premium = round(sum(l.deposit_premium for l in layers), 6)
    rec.rol = round(rec.deposit_premium / total_limit, 6)
    rec.written_line = by_limit(lambda l: l.written_line)
    rec.signed_line = by_limit(lambda l: l.signed_line)
    if rec.deposit_premium > 0:
        rec.brokerage_pct = round(sum(l.deposit_premium * l.brokerage_pct for l in layers) / rec.deposit_premium, 6)
    attachments = [l.attachment for l in layers if l.attachment is not None]
    if attachments:
        rec.attachment = min(attachments)
    _sync_profile_rarc_from_layers(rec, layers, clear_if_empty=clear_rarc_if_empty)


class ContractStore:
    """SQLAlchemy-backed CMS Contract Ledger."""

    def ensure_seeded(self, default_sim_set_id: str) -> None:
        with get_session() as db:
            if db.query(ContractRecord).count() > 0:
                return
            for idx, prog in enumerate((
                _build_farmers_programme(default_sim_set_id),
                _build_pacific_eq_programme(default_sim_set_id),
                _build_eurowind_programme(default_sim_set_id),
            ), start=1):
                is_farmers = "farmers" in prog.programme_id
                is_pacific = "pacific" in prog.programme_id
                ee_id = "ee-prog-farmers-27" if is_farmers else ("ee-prog-pacific-27" if is_pacific else None)
                profile = ProgrammeProfile(
                    unique_code=f"RL-{prog.treaty_year}-{idx:03d}",
                    program_code=f"PGM-{idx:03d}",
                    legal_entity="RoaringLion Re Transformer",
                    trading_unit="N.A. Property" if (is_farmers or is_pacific) else "International Property",
                    planning_class="Property CAT",
                    new_or_renewal="RENEWAL",
                    is_multi_year=False,
                    written_date="2026-11-18",
                    external_file_path=f"https://docs.internal/reinsurance/{prog.treaty_year}/{prog.programme_id}",
                    cedent_group=prog.cedent_name,
                    cedent_country="USA" if is_farmers else ("USA / JP" if is_pacific else "DE"),
                    cedent_credit_rating="AM Best A" if is_farmers else ("AM Best A-" if is_pacific else "S&P A+"),
                    broker_group=prog.broker,
                    closing_broker=prog.broker,
                    broker_contact="Lead Treaty Desk",
                    pricing_analyst="Cat Modeling Desk",
                    offered_line=prog.written_line,
                    estimated_signed_pct=1.0,
                    limit_basis="UNL",
                    claim_trigger="LOSSES_OCCURRING",
                    loss_event_type="1ST_LOSS",
                    reinstatement_basis="PRO_RATA_AMOUNT",
                    inuring_note="Underlying QS inures prior to 1st Cat XL" if is_farmers else "Clean occurrence tower",
                    economic_inflation_pct=3.5 if is_farmers else 2.5,
                    social_inflation_pct=1.2 if is_farmers else 0.0,
                    gearing_effect_pct=14.5 if is_farmers else 6.0,
                    rarc_exposure_pct=1.8 if is_farmers else 0.6,
                    rarc_layer_pct=-2.1 if is_farmers else 0.4,
                    rarc_price_pct=6.4 if is_farmers else (4.2 if is_pacific else 3.1),
                    rarc_total_pct=6.1 if is_farmers else (5.2 if is_pacific else 4.1),
                    prior_year_limit_m=round(prog.limit * 0.94, 2),
                    prior_year_premium_m=round(prog.deposit_premium * 0.91, 2),
                    prior_year_rol_pct=round(prog.rol * 94.0, 2),
                    prior_year_signed_line=prog.signed_line,
                )
                chash = canonical_hash(prog.model_dump())
                chain_key = "chain-prog-farmers-us" if is_farmers else f"chain-{prog.programme_id}"
                prior_id = "prog-farmers-us-26" if is_farmers else None
                rec = ContractRecord(
                    programme_id=prog.programme_id,
                    chain_id=chain_key,
                    prior_contract_id=prior_id,
                    cedent_name=prog.cedent_name,
                    programme_name=prog.programme_name,
                    treaty_year=prog.treaty_year,
                    renewal_period=prog.renewal_period,
                    office=prog.office,
                    underwriter=prog.underwriter,
                    broker=prog.broker,
                    fronting_carrier="RoaringLion Re Transformer",
                    collateral_cell=f"Cell-{prog.renewal_period}",
                    status=prog.status.value,
                    direction=ContractDirection.ASSUMED.value,
                    structure=StructureType.OCC_XL.value,
                    instrument=InstrumentType.UNL.value,
                    placement=PlacementType.OPEN_MARKET.value,
                    inception_date=f"{prog.treaty_year}-01-01",
                    expiry_date=f"{prog.treaty_year}-12-31",
                    hours_clause=72,
                    written_line=prog.written_line,
                    signed_line=prog.signed_line,
                    limit=prog.limit,
                    attachment=80.0 if is_farmers else (70.0 if is_pacific else 45.0),
                    deposit_premium=prog.deposit_premium,
                    rol=prog.rol,
                    brokerage_pct=0.10,
                    fronting_fee_pct=0.02,
                    collateral_required=prog.collateral_required,
                    currency=prog.currency,
                    sim_set_id=prog.sim_set_id,
                    exposure_eclipse_programme_id=ee_id,
                    region_perils_json=json.dumps(prog.region_perils),
                    perils_json=json.dumps([p.value for p in prog.perils]),
                    custom_tags_json=json.dumps(["Peak" if "US-FL-WS" in prog.region_perils else "Diversifier"]),
                    uplift_set_json=prog.uplift_set.model_dump_json(),
                    graph_json=prog.graph.model_dump_json(),
                    profile_json=profile.model_dump_json(),
                    version=1,
                    content_hash=chash,
                )
                db.add(rec)
                if is_farmers:
                    prior_profile = ProgrammeProfile(
                        unique_code="RL-2026-001",
                        program_code="PGM-001",
                        legal_entity="RoaringLion Re Transformer",
                        trading_unit="N.A. Property",
                        planning_class="Property CAT",
                        new_or_renewal="RENEWAL",
                        written_date="2025-11-20",
                        cedent_group=prog.cedent_name,
                        cedent_country="USA",
                        cedent_credit_rating="AM Best A",
                        broker_group=prog.broker,
                        closing_broker=prog.broker,
                        broker_contact="Lead Treaty Desk",
                        pricing_analyst="Cat Modeling Desk",
                        offered_line=1.0,
                        estimated_signed_pct=1.0,
                        limit_basis="UNL",
                        claim_trigger="LOSSES_OCCURRING",
                        loss_event_type="1ST_LOSS",
                        reinstatement_basis="PRO_RATA_AMOUNT",
                        inuring_note="Underlying QS inures prior to 1st Cat XL",
                        economic_inflation_pct=3.0,
                        rarc_total_pct=4.5,
                    )
                    prior_uplift = prog.uplift_set.model_copy(
                        update={"uplift_set_id": "upl-farmers-us-26", "name": "Farmers US 2026 House Uplift"}
                    )
                    prior_graph = prog.graph.model_copy(
                        deep=True,
                        update={"graph_id": "grp-farmers-us-26", "name": "Farmers US Wind Tower 2026"},
                    )
                    for n in prior_graph.nodes:
                        if n.node_type in _LAYER_TYPES:
                            n.params = {**n.params, "rarcPct": 4.5}
                    db.add(
                        ContractRecord(
                            programme_id="prog-farmers-us-26",
                            chain_id="chain-prog-farmers-us",
                            prior_contract_id=None,
                            cedent_name=prog.cedent_name,
                            programme_name="US Wind Occ + Agg Tower (2026 Expiring)",
                            treaty_year=2026,
                            renewal_period="J26",
                            office=prog.office,
                            underwriter=prog.underwriter,
                            broker=prog.broker,
                            fronting_carrier="RoaringLion Re Transformer",
                            collateral_cell="Cell-J26",
                            status=ProgrammeStatus.EXPIRED.value,
                            direction=ContractDirection.ASSUMED.value,
                            structure=StructureType.OCC_XL.value,
                            instrument=InstrumentType.UNL.value,
                            placement=PlacementType.OPEN_MARKET.value,
                            inception_date="2026-01-01",
                            expiry_date="2026-12-31",
                            hours_clause=72,
                            written_line=1.0,
                            signed_line=1.0,
                            limit=80.0,
                            attachment=75.0,
                            deposit_premium=17.6,
                            rol=0.22,
                            brokerage_pct=0.10,
                            fronting_fee_pct=0.02,
                            collateral_required=80.0,
                            currency=prog.currency,
                            sim_set_id=prog.sim_set_id,
                            exposure_eclipse_programme_id=ee_id,
                            region_perils_json=json.dumps(prog.region_perils),
                            perils_json=json.dumps([p.value for p in prog.perils]),
                            custom_tags_json=json.dumps(["Peak", "Expiring"]),
                            uplift_set_json=prior_uplift.model_dump_json(),
                            graph_json=prior_graph.model_dump_json(),
                            profile_json=prior_profile.model_dump_json(),
                            version=1,
                            content_hash=canonical_hash({"id": "prog-farmers-us-26", "year": 2026}),
                        )
                    )
                    db.add(
                        ContractNoteRecord(
                            note_id="note-seed-farmers-1",
                            programme_id=prog.programme_id,
                            cedent_name=prog.cedent_name,
                            author=prog.underwriter,
                            category="UNDERWRITING",
                            body="FL Wind house uplift +10% applied (Adjusted view). Attachment raised +5m YoY; risk-adjusted rate change +6.1% meets fund hurdle.",
                        )
                    )
            db.commit()

        # Seed a bound ILW hedge (auto-wired to Industry Loss Run) and a quoted UNL retro hedge
        # (auto-wired to our own UNL Portfolio Snapshot) so both live as regular Contracts programmes.
        existing = self.list_contracts()
        seed_lineage = LineageRegistry(persist=False)
        self.create_contract(
            ContractCreate(
                programme_id="prog-ilw-fl-wind-27",
                cedent_name="ILW Market Protection",
                programme_name="US FL Wind $40B Binary ILW",
                treaty_year=2027,
                renewal_period="J27",
                office="BDA",
                underwriter="Lead Underwriter",
                broker="Broker C",
                status=ProgrammeStatus.BOUND,
                direction=ContractDirection.EXTERNAL_RETRO,
                is_hedge=True,
                structure=StructureType.ILW,
                instrument=InstrumentType.INDUSTRY_INDEX,
                placement=PlacementType.PRIVATE,
                inception_date="2027-01-01",
                expiry_date="2027-12-31",
                limit=25.0,
                attachment=40.0,
                deposit_premium=3.5,
                rol=0.14,
                brokerage_pct=0.0,
                collateral_required=0.0,
                currency="USD",
                sim_set_id=DEFAULT_INDUSTRY_SIM_SET_ID,
                region_perils=["US-FL-WS"],
                perils=[PerilCode.WS],
                custom_tags=["ILW", "Hedge", "FL Wind"],
                ilw_spec={
                    "industrySimSetId": DEFAULT_INDUSTRY_SIM_SET_ID,
                    "regionPerils": ["US-FL-WS"],
                    "triggerType": "BINARY",
                    "triggerBn": 40.0,
                    "limitM": 25.0,
                    "rol": 0.14,
                    "direction": "EXTERNAL_RETRO",
                },
                profile=ProgrammeProfile(
                    unique_code="RL-2027-004",
                    program_code="PGM-004",
                    legal_entity="RoaringLion Re Transformer",
                    trading_unit="Retro & Hedging",
                    planning_class="ILW Hedge",
                    new_or_renewal="NEW",
                    written_date="2026-11-22",
                    cedent_group="RoaringLion Cat Fund (Ceded)",
                    cedent_country="BMU",
                    cedent_credit_rating="Collateralized",
                    broker_group="Broker C",
                    limit_basis="INDUSTRY_INDEX",
                    inuring_note="PCS Florida Wind industry loss warranty hedging peak FL tail.",
                    rarc_total_pct=-2.5,
                ),
            ),
            seed_lineage,
            _existing_contracts=existing,
        )
        self.create_contract(
            ContractCreate(
                programme_id="prog-retro-unl-27",
                cedent_name="RoaringLion UNL Book",
                programme_name="Whole-Account UNL Retro XL",
                treaty_year=2027,
                renewal_period="J27",
                office="BDA",
                underwriter="Lead Underwriter",
                broker="Broker A",
                status=ProgrammeStatus.QUOTED,
                direction=ContractDirection.EXTERNAL_RETRO,
                is_hedge=True,
                structure=StructureType.OCC_XL,
                instrument=InstrumentType.UNL,
                placement=PlacementType.PRIVATE,
                inception_date="2027-01-01",
                expiry_date="2027-12-31",
                portfolio_snapshot_date="2027-01-01",
                limit=35.0,
                attachment=45.0,
                deposit_premium=3.5,
                rol=0.10,
                brokerage_pct=0.0,
                collateral_required=0.0,
                currency="USD",
                sim_set_id=default_sim_set_id,
                region_perils=[],
                perils=[PerilCode.WS, PerilCode.EQ],
                custom_tags=["Retro", "Hedge", "UNL"],
                profile=ProgrammeProfile(
                    unique_code="RL-2027-005",
                    program_code="PGM-005",
                    legal_entity="RoaringLion Re Transformer",
                    trading_unit="Retro & Hedging",
                    planning_class="UNL Retrocession",
                    new_or_renewal="NEW",
                    written_date="2026-11-24",
                    cedent_group="RoaringLion Cat Fund (Ceded)",
                    cedent_country="BMU",
                    cedent_credit_rating="AM Best A",
                    broker_group="Broker A",
                    limit_basis="UNL",
                    inuring_note="Occurrence retrocession attaching over our own bound UNL portfolio snapshot as of 2027-01-01.",
                    rarc_total_pct=-1.0,
                ),
            ),
            seed_lineage,
            _existing_contracts=existing,
        )

    def list_contracts(
        self,
        *,
        status: ProgrammeStatus | None = None,
        direction: ContractDirection | None = None,
        underwriter: str | None = None,
        office: str | None = None,
        renewal_period: str | None = None,
        search: str | None = None,
    ) -> list[ContractFull]:
        with get_session() as db:
            q = db.query(ContractRecord)
            if status:
                q = q.filter(ContractRecord.status == status.value)
            if direction:
                q = q.filter(ContractRecord.direction == direction.value)
            if underwriter:
                q = q.filter(ContractRecord.underwriter == underwriter)
            if office:
                q = q.filter(ContractRecord.office == office)
            if renewal_period:
                q = q.filter(ContractRecord.renewal_period == renewal_period)
            recs = q.order_by(ContractRecord.updated_at.desc()).all()
            out = [_record_to_full(r) for r in recs]
            if search:
                s = search.lower()
                out = [
                    c for c in out
                    if s in c.cedent_name.lower()
                    or s in c.programme_name.lower()
                    or s in c.broker.lower()
                    or s in c.programme_id.lower()
                ]
            return out

    def query_contracts(
        self,
        *,
        statuses: Iterable[ProgrammeStatus] | None = None,
        programme_ids: Iterable[str] | None = None,
        exclude_structures: Iterable[StructureType] = (),
        search: str | None = None,
        order_by: Literal["RECENT", "NAME"] = "RECENT",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[ContractFull], int]:
        """Filter, search and page contracts in SQL. Returns (page, total rows matching the filters).

        `statuses` / `programme_ids` of None mean "no filter"; an empty collection matches nothing.
        """
        status_values = None if statuses is None else sorted({s.value for s in statuses})
        id_values = None if programme_ids is None else sorted(set(programme_ids))
        if status_values == [] or id_values == []:
            return [], 0
        with get_session() as db:
            q = db.query(ContractRecord)
            if status_values is not None:
                q = q.filter(ContractRecord.status.in_(status_values))
            if id_values is not None:
                q = q.filter(ContractRecord.programme_id.in_(id_values))
            excluded = [s.value for s in exclude_structures]
            if excluded:
                q = q.filter(ContractRecord.structure.notin_(excluded))
            term = (search or "").strip()
            if term:
                pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                q = q.filter(
                    or_(
                        ContractRecord.cedent_name.ilike(pattern, escape="\\"),
                        ContractRecord.programme_name.ilike(pattern, escape="\\"),
                        ContractRecord.broker.ilike(pattern, escape="\\"),
                        ContractRecord.programme_id.ilike(pattern, escape="\\"),
                    )
                )
            total = q.count()
            if order_by == "NAME":
                q = q.order_by(ContractRecord.cedent_name, ContractRecord.programme_name, ContractRecord.programme_id)
            else:
                q = q.order_by(ContractRecord.updated_at.desc(), ContractRecord.programme_id)
            if offset:
                q = q.offset(offset)
            if limit is not None:
                q = q.limit(limit)
            return [_record_to_full(r) for r in q.all()], total

    def count_by_status(self, *, exclude_structures: Iterable[StructureType] = ()) -> dict[ProgrammeStatus, int]:
        """Number of contracts per status, counted in SQL."""
        with get_session() as db:
            q = db.query(ContractRecord.status, func.count(ContractRecord.programme_id))
            excluded = [s.value for s in exclude_structures]
            if excluded:
                q = q.filter(ContractRecord.structure.notin_(excluded))
            return {ProgrammeStatus(status): int(n) for status, n in q.group_by(ContractRecord.status).all()}

    def get_contract(self, programme_id: str) -> ContractFull | None:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            return _record_to_full(rec) if rec else None

    def create_contract(
        self,
        payload: ContractCreate,
        lineage: LineageRegistry,
        *,
        _existing_contracts: list[ContractFull] | None = None,
    ) -> ContractFull:
        prog_id = payload.programme_id or f"prog-{uuid.uuid4().hex[:8]}"
        direction = payload.direction
        if payload.is_hedge is True and not is_hedge_direction(direction):
            direction = ContractDirection.EXTERNAL_RETRO
        elif payload.is_hedge is False and is_hedge_direction(direction):
            direction = ContractDirection.ASSUMED

        structure = payload.structure
        instrument = payload.instrument
        sim_set_id = payload.sim_set_id
        if is_ilw_programme(structure, instrument):
            structure = StructureType.ILW
            instrument = InstrumentType.INDUSTRY_INDEX
            if sim_set_id == "sim-synth-cat-v1-100k":
                sim_set_id = DEFAULT_INDUSTRY_SIM_SET_ID

        snapshot_date = payload.portfolio_snapshot_date
        if is_unl_retro_hedge(direction, structure, instrument) and not snapshot_date:
            snapshot_date = payload.inception_date or f"{payload.treaty_year}-01-01"

        normalized_payload = payload.model_copy(
            update={
                "direction": direction,
                "is_hedge": is_hedge_direction(direction),
                "structure": structure,
                "instrument": instrument,
                "sim_set_id": sim_set_id,
                "portfolio_snapshot_date": snapshot_date,
            }
        )

        uplift = normalized_payload.uplift_set or UpliftSet(
            uplift_set_id=f"upl-{prog_id}",
            name=f"{normalized_payload.programme_name} Uplift",
            version=1,
            rules=[],
            content_hash=canonical_hash([]),
        )
        need_existing = normalized_payload.graph is not None or is_unl_retro_hedge(direction, structure, instrument)
        all_existing = (
            _existing_contracts
            if _existing_contracts is not None
            else (self.list_contracts() if need_existing else [])
        )
        graph = normalized_payload.graph or _default_graph_for_contract(
            normalized_payload,
            prog_id,
            all_contracts=all_existing,
        )
        if normalized_payload.graph is not None:
            ensure_layer_codes(graph, strict=True)
            validate_pricing_links_one_to_one(prog_id, graph, all_existing)
        collateral = (
            0.0
            if is_hedge_direction(direction) and "collateral_required" not in payload.model_fields_set
            else normalized_payload.collateral_required
        )
        chash = canonical_hash({
            "programme_id": prog_id,
            "direction": direction.value,
            "structure": structure.value,
            "limit": normalized_payload.limit,
            "signed_line": normalized_payload.signed_line,
            "rol": normalized_payload.rol,
            "graph": graph.content_hash,
            "uplift": uplift.content_hash,
        })
        rec = ContractRecord(
            programme_id=prog_id,
            chain_id=normalized_payload.chain_id or f"chain-{prog_id}",
            prior_contract_id=normalized_payload.prior_contract_id,
            cedent_name=normalized_payload.cedent_name,
            programme_name=normalized_payload.programme_name,
            treaty_year=normalized_payload.treaty_year,
            renewal_period=normalized_payload.renewal_period,
            office=normalized_payload.office,
            underwriter=normalized_payload.underwriter,
            secondary_underwriter=normalized_payload.secondary_underwriter,
            broker=normalized_payload.broker,
            fronting_carrier=normalized_payload.fronting_carrier,
            collateral_cell=normalized_payload.collateral_cell,
            status=normalized_payload.status.value,
            direction=direction.value,
            structure=structure.value,
            instrument=instrument.value,
            placement=normalized_payload.placement.value,
            inception_date=normalized_payload.inception_date,
            expiry_date=normalized_payload.expiry_date,
            portfolio_snapshot_date=snapshot_date,
            hours_clause=normalized_payload.hours_clause,
            written_line=normalized_payload.written_line,
            signed_line=normalized_payload.signed_line,
            limit=normalized_payload.limit,
            attachment=normalized_payload.attachment,
            deposit_premium=normalized_payload.deposit_premium,
            rol=normalized_payload.rol,
            brokerage_pct=normalized_payload.brokerage_pct,
            fronting_fee_pct=normalized_payload.fronting_fee_pct,
            collateral_required=collateral,
            currency=normalized_payload.currency,
            sim_set_id=sim_set_id,
            exposure_eclipse_programme_id=normalized_payload.exposure_eclipse_programme_id,
            region_perils_json=json.dumps(normalized_payload.region_perils),
            perils_json=json.dumps([p.value for p in normalized_payload.perils]),
            custom_tags_json=json.dumps(normalized_payload.custom_tags),
            uplift_set_json=uplift.model_dump_json(),
            graph_json=graph.model_dump_json(),
            ilw_spec_json=json.dumps(normalized_payload.ilw_spec) if normalized_payload.ilw_spec else None,
            profile_json=(normalized_payload.profile or ProgrammeProfile(unique_code=f"RL-{normalized_payload.treaty_year}-{prog_id[-6:].upper()}", cedent_group=normalized_payload.cedent_name)).model_dump_json(),
            version=1,
            content_hash=chash,
        )
        if normalized_payload.layers and normalized_payload.graph is None:
            _roll_up_layers(rec, graph)
            rec.collateral_required = 0.0 if is_hedge_direction(direction) else (rec.limit * rec.signed_line)
        else:
            _sync_profile_rarc_from_layers(rec, _active_rollup_layers(rec, graph))
        with get_session() as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
            full = _record_to_full(rec)

        lineage.add_link(prog_id, sim_set_id, LinkType.USES_LOSSES)
        lineage.add_link(prog_id, graph.graph_id, LinkType.USES_STRUCTURE)
        return full

    def update_contract(self, programme_id: str, patch: ContractUpdate) -> ContractFull | None:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            data = patch.model_dump(exclude_unset=True)
            rewire_needed = False
            if "is_hedge" in data and data["is_hedge"] is not None:
                want_hedge = bool(data.pop("is_hedge"))
                if "direction" not in data or data["direction"] is None:
                    data["direction"] = (
                        ContractDirection.EXTERNAL_RETRO if want_hedge else ContractDirection.ASSUMED
                    )
            else:
                data.pop("is_hedge", None)

            for k, v in data.items():
                if k == "custom_tags":
                    rec.custom_tags_json = json.dumps(v)
                elif k == "profile" and v is not None:
                    prev_prof = ProgrammeProfile(**json.loads(rec.profile_json)) if rec.profile_json else ProgrammeProfile()
                    next_prof = ProgrammeProfile(**v)
                    next_prof.rarc_total_pct = prev_prof.rarc_total_pct
                    rec.profile_json = next_prof.model_dump_json()
                    _sync_profile_rarc_from_layers(
                        rec,
                        _active_rollup_layers(rec, GraphSpec(**json.loads(rec.graph_json))),
                    )
                elif hasattr(v, "value"):
                    setattr(rec, k, v.value)
                else:
                    setattr(rec, k, v)
                if k in ("direction", "structure", "instrument", "portfolio_snapshot_date", "sim_set_id"):
                    rewire_needed = True

            cur_dir = ContractDirection(rec.direction)
            cur_struct = StructureType(rec.structure)
            cur_inst = InstrumentType(rec.instrument)
            if is_ilw_programme(cur_struct, cur_inst):
                rec.structure = StructureType.ILW.value
                rec.instrument = InstrumentType.INDUSTRY_INDEX.value
                cur_struct = StructureType.ILW
                cur_inst = InstrumentType.INDUSTRY_INDEX
                if rec.sim_set_id == "sim-synth-cat-v1-100k":
                    rec.sim_set_id = DEFAULT_INDUSTRY_SIM_SET_ID
            elif is_unl_retro_hedge(cur_dir, cur_struct, cur_inst):
                if not rec.portfolio_snapshot_date:
                    rec.portfolio_snapshot_date = rec.inception_date or f"{rec.treaty_year}-01-01"

            if "direction" in data and "collateral_required" not in data:
                rec.collateral_required = 0.0 if is_hedge_direction(cur_dir) else round(rec.limit * rec.signed_line, 6)

            if rewire_needed:
                all_contracts = [_record_to_full(r) for r in db.query(ContractRecord).all()]
                graph = GraphSpec(**json.loads(rec.graph_json))
                graph_changed = _auto_wire_graph_source(
                    graph,
                    direction=cur_dir,
                    structure=cur_struct,
                    instrument=cur_inst,
                    sim_set_id=rec.sim_set_id,
                    portfolio_snapshot_date=rec.portfolio_snapshot_date,
                    inception_date=rec.inception_date,
                    all_contracts=all_contracts,
                    programme_id=rec.programme_id,
                )
                # Keep single-layer node type aligned when toggling to/from ILW structure
                layer_nodes = [n for n in graph.nodes if n.node_type in _LAYER_TYPES]
                if "structure" in data and len(layer_nodes) == 1:
                    ln = layer_nodes[0]
                    if cur_struct == StructureType.ILW and ln.node_type in (NodeType.OCC_XL, NodeType.AGG_XL):
                        lim_val = ln.params.get("occLimit") or ln.params.get("aggLimit") or rec.limit
                        att_val = ln.params.get("occAttachment") or ln.params.get("aggAttachment") or rec.attachment
                        ln.node_type = NodeType.ILW_OPTION
                        ln.params = {
                            "layerCode": ln.params.get("layerCode", "L1"),
                            "labelName": ln.params.get("labelName") or rec.programme_name,
                            "limit": float(lim_val),
                            "trigger": float(att_val),
                            "triggerType": "BINARY",
                            "depositPremium": float(ln.params.get("depositPremium", rec.deposit_premium) or 0.0),
                            "placedShare": float(ln.params.get("placedShare", rec.signed_line) or 1.0),
                            "writtenLine": float(ln.params.get("writtenLine", rec.written_line) or 1.0),
                        }
                        graph_changed = True
                if graph_changed:
                    relabel_graph(graph)
                    graph.version += 1
                    graph.content_hash = canonical_hash({
                        "nodes": [n.model_dump() for n in graph.nodes],
                        "edges": [e.model_dump() for e in graph.edges],
                    })
                    rec.graph_json = graph.model_dump_json()

            rec.version += 1
            rec.content_hash = canonical_hash({
                "programme_id": rec.programme_id,
                "version": rec.version,
                "status": rec.status,
                "direction": rec.direction,
                "structure": rec.structure,
                "signed_line": rec.signed_line,
                "limit": rec.limit,
                "rol": rec.rol,
            })
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            return _record_to_full(rec)

    def update_uplifts(self, programme_id: str, rules: list[UpliftRule]) -> ContractFull | None:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            uplift = UpliftSet(**json.loads(rec.uplift_set_json))
            uplift.version += 1
            uplift.rules = rules
            uplift.content_hash = canonical_hash([r.model_dump() for r in rules])
            rec.uplift_set_json = uplift.model_dump_json()
            rec.version += 1
            rec.content_hash = canonical_hash({"programme_id": rec.programme_id, "version": rec.version, "uplift": uplift.content_hash})
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            return _record_to_full(rec)

    def update_layers(self, programme_id: str, updates: list[ContractLayerUpdate]) -> ContractFull | None:
        clear_rarc = any("rarc_pct" in u.model_fields_set for u in updates)
        return self._edit_layers(
            programme_id,
            lambda graph, rec: apply_layer_updates(graph, updates),
            clear_rarc_if_empty=clear_rarc,
        )

    def add_layer(self, programme_id: str, spec: ContractLayerSpec) -> ContractFull | None:
        def edit(graph: GraphSpec, rec: ContractRecord) -> GraphSpec:
            add_layer_to_graph(graph, spec, rec.written_line, rec.signed_line, programme_id=programme_id)
            return graph

        return self._edit_layers(programme_id, edit)

    def remove_layer(self, programme_id: str, node_id: str) -> ContractFull | None:
        def edit(graph: GraphSpec, rec: ContractRecord) -> GraphSpec:
            remove_layer_from_graph(graph, node_id, programme_id=programme_id)
            return graph

        return self._edit_layers(programme_id, edit, clear_rarc_if_empty=True)

    def _edit_layers(self, programme_id: str, edit, *, clear_rarc_if_empty: bool = False) -> ContractFull | None:
        from app.engine.slice_runner import validate_dag_acyclic

        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            graph = edit(GraphSpec(**json.loads(rec.graph_json)), rec)
            validate_dag_acyclic(graph)
            graph.version += 1
            graph.content_hash = canonical_hash({
                "nodes": [n.model_dump() for n in graph.nodes],
                "edges": [e.model_dump() for e in graph.edges],
            })
            rec.graph_json = graph.model_dump_json()

            _roll_up_layers(rec, graph, clear_rarc_if_empty=clear_rarc_if_empty)

            rec.version += 1
            rec.content_hash = canonical_hash({"programme_id": rec.programme_id, "version": rec.version, "graph": graph.content_hash})
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            return _record_to_full(rec)

    def update_graph(self, programme_id: str, graph: GraphSpec) -> ContractFull | None:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            all_contracts = [_record_to_full(r) for r in db.query(ContractRecord).all()]
            old_graph = GraphSpec(**json.loads(rec.graph_json))
            for node in graph.nodes:
                if node.node_type == NodeType.PORTFOLIO_ROLLUP:
                    as_of = str(node.params.get("asOfDate") or rec.portfolio_snapshot_date or rec.inception_date or "2027-01-01").strip()
                    rec.portfolio_snapshot_date = as_of
                    snap_contracts = select_unl_snapshot_contracts(
                        all_contracts,
                        as_of_date=as_of,
                        exclude_programme_id=programme_id,
                    )
                    p = dict(node.params)
                    p["asOfDate"] = as_of
                    p["sourceKind"] = "UNL_PORTFOLIO_SNAPSHOT"
                    p["lossFiles"] = snapshot_loss_files(
                        snap_contracts, as_of_date=as_of, fallback_sim_set_id=rec.sim_set_id
                    )
                    node.params = p
                elif node.node_type == NodeType.INDUSTRY_INDEX_SOURCE:
                    ind_sim_id = str(node.params.get("simSetId") or DEFAULT_INDUSTRY_SIM_SET_ID).strip()
                    p = dict(node.params)
                    p["simSetId"] = ind_sim_id
                    p["sourceKind"] = "INDUSTRY_LOSS_RUN"
                    ind_files = _sim_loss_files(db).get(ind_sim_id)
                    if ind_files is not None:
                        p["lossFiles"] = ind_files
                    node.params = p
            ensure_layer_codes(graph, strict=True)
            validate_pricing_output_single_connection(graph)
            validate_filters_match_loss_files(graph, _sim_loss_files(db), rec.sim_set_id)
            validate_pricing_links_one_to_one(programme_id, graph, all_contracts)
            relabel_graph(graph)
            graph.version = old_graph.version + 1
            graph.content_hash = canonical_hash({
                "nodes": [n.model_dump() for n in graph.nodes],
                "edges": [e.model_dump() for e in graph.edges],
            })
            rec.graph_json = graph.model_dump_json()
            _roll_up_layers(rec, graph)
            rec.version += 1
            rec.content_hash = canonical_hash({"programme_id": rec.programme_id, "version": rec.version, "graph": graph.content_hash})
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            return _record_to_full(rec)

    def bind_deal(self, programme_id: str, lineage: LineageRegistry) -> tuple[ContractFull, str] | None:
        from app.models.domain import BreadcrumbStep

        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            rec.status = ProgrammeStatus.BOUND.value
            rec.version += 1
            rec.content_hash = canonical_hash({"programme_id": rec.programme_id, "version": rec.version, "status": "BOUND"})
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            full = _record_to_full(rec)

        manifest = lineage.record_manifest(
            run_type="DEAL_BIND",
            input_hashes={"contract": full.content_hash, "graph": full.graph.content_hash, "uplift": full.uplift_set.content_hash},
            parameters={"programme_id": programme_id, "signed_line": full.signed_line, "rol": full.rol},
            output_payload={"status": "BOUND", "version": full.version},
            breadcrumb=[
                BreadcrumbStep(entity_type="PROGRAMME", entity_id=full.programme_id, label=f"{full.cedent_name} — {full.programme_name}", version=f"v{full.version}", content_hash=full.content_hash),
                BreadcrumbStep(entity_type="DAG_GRAPH", entity_id=full.graph.graph_id, label=full.graph.name, version=f"v{full.graph.version}", content_hash=full.graph.content_hash),
            ],
        )
        lineage.add_link(full.programme_id, manifest.manifest_id, LinkType.BOUND_AS)
        return full, manifest.manifest_id

    def promote_ilw(self, req: IlwPriceRequest, lineage: LineageRegistry) -> ContractFull:
        payload = ContractCreate(
            cedent_name="ILW Market Counterparty",
            programme_name=req.name,
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Broker C",
            status=ProgrammeStatus.BOUND,
            direction=req.direction,
            structure=StructureType.ILW,
            instrument=InstrumentType.INDUSTRY_INDEX,
            placement=PlacementType.PRIVATE,
            limit=req.limit_m,
            attachment=req.trigger_bn,
            deposit_premium=round(req.limit_m * req.rol, 4),
            rol=req.rol,
            collateral_required=req.limit_m if req.direction == ContractDirection.ASSUMED else 0.0,
            currency=req.currency,
            sim_set_id=req.industry_sim_set_id,
            region_perils=req.region_perils,
            perils=[PerilCode.WS if "WS" in rp else PerilCode.EQ for rp in req.region_perils[:1]],
            custom_tags=["ILW", req.trigger_type.value],
            ilw_spec=req.model_dump(),
        )
        return self.create_contract(payload, lineage)

    def select_slice(self, req: SliceRollupRequest) -> list[ContractFull]:
        all_c = self.list_contracts()
        out: list[ContractFull] = []
        for c in all_c:
            if req.programme_ids and c.programme_id not in req.programme_ids:
                continue
            if req.statuses and c.status not in req.statuses:
                continue
            if req.directions and c.direction not in req.directions:
                continue
            if req.underwriters and c.underwriter not in req.underwriters:
                continue
            if req.offices and c.office not in req.offices:
                continue
            if req.renewal_periods and c.renewal_period not in req.renewal_periods:
                continue
            if req.region_perils and not any(rp in c.region_perils for rp in req.region_perils):
                continue
            out.append(c)
        return out

    def list_notes(self, programme_id: str, scope: str = "programme") -> list[ContractNoteOut]:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return []
            q = db.query(ContractNoteRecord)
            if scope == "cedent":
                q = q.filter(ContractNoteRecord.cedent_name == rec.cedent_name)
            else:
                q = q.filter(ContractNoteRecord.programme_id == programme_id)
            rows = q.order_by(ContractNoteRecord.created_at.desc()).all()
            return [
                ContractNoteOut(
                    note_id=n.note_id,
                    programme_id=n.programme_id,
                    cedent_name=n.cedent_name,
                    author=n.author,
                    category=n.category,
                    body=n.body,
                    created_at=n.created_at,
                )
                for n in rows
            ]

    def add_note(self, programme_id: str, payload: ContractNoteCreate) -> ContractNoteOut | None:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            note = ContractNoteRecord(
                note_id=f"note-{uuid.uuid4().hex[:8]}",
                programme_id=programme_id,
                cedent_name=rec.cedent_name,
                author=payload.author.strip() or "Lead Underwriter",
                category=payload.category.upper(),
                body=payload.body.strip(),
            )
            db.add(note)
            db.commit()
            db.refresh(note)
            return ContractNoteOut(
                note_id=note.note_id,
                programme_id=note.programme_id,
                cedent_name=note.cedent_name,
                author=note.author,
                category=note.category,
                body=note.body,
                created_at=note.created_at,
            )

    def link_prior_programme(
        self,
        programme_id: str,
        prior_contract_id: str | None,
        lineage: LineageRegistry,
    ) -> ContractFull | None:
        """Link or unlink a programme to its prior underwriting year, unifying chain_id and copying prior-year metrics."""
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None

            if prior_contract_id is None or prior_contract_id == "":
                rec.prior_contract_id = None
                rec.version += 1
                rec.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(rec)
                return _record_to_full(rec)

            if prior_contract_id == programme_id:
                raise ValueError("A programme cannot link to itself as prior year")

            prior = db.get(ContractRecord, prior_contract_id)
            if not prior:
                raise KeyError(f"Prior programme not found: {prior_contract_id}")

            # Cycle check: walk prior's ancestors to ensure programme_id is not already an ancestor
            cursor = prior.prior_contract_id
            seen = {programme_id, prior_contract_id}
            while cursor:
                if cursor in seen:
                    raise ValueError("Linking these programmes would create a renewal cycle")
                seen.add(cursor)
                anc = db.get(ContractRecord, cursor)
                cursor = anc.prior_contract_id if anc else None

            old_chain = rec.chain_id
            target_chain = prior.chain_id or f"chain-{prior.programme_id}"
            prior.chain_id = target_chain
            rec.prior_contract_id = prior_contract_id
            rec.chain_id = target_chain

            # Also bring any descendants of rec onto the unified chain_id
            if old_chain and old_chain != target_chain:
                for desc in db.query(ContractRecord).filter(ContractRecord.chain_id == old_chain).all():
                    desc.chain_id = target_chain

            prof = ProgrammeProfile(**json.loads(rec.profile_json)) if rec.profile_json else ProgrammeProfile()
            prof.prior_year_limit_m = prior.limit
            prof.prior_year_premium_m = prior.deposit_premium
            prof.prior_year_rol_pct = round(prior.rol * 100.0, 2)
            prof.prior_year_signed_line = prior.signed_line
            rec.profile_json = prof.model_dump_json()

            rec.version += 1
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            out = _record_to_full(rec)

        lineage.add_link(programme_id, prior_contract_id, LinkType.SUPERSEDES)
        return out

    def renew_programme(
        self,
        programme_id: str,
        req: RenewProgrammeRequest,
        lineage: LineageRegistry,
    ) -> ContractFull | None:
        """Clone an existing programme into the next underwriting year, linked via prior_contract_id + shared chain_id."""
        with get_session() as db:
            src = db.get(ContractRecord, programme_id)
            if not src:
                return None

            next_year = req.treaty_year or (src.treaty_year + 1)
            next_period = req.renewal_period
            if not next_period:
                yr_short = str(next_year)[-2:]
                prefix = "".join(ch for ch in src.renewal_period if ch.isalpha()) or "J"
                next_period = f"{prefix}{yr_short}"

            new_id = f"prog-{uuid.uuid4().hex[:8]}"
            chain_id = src.chain_id or f"chain-{src.programme_id}"
            src.chain_id = chain_id

            new_limit = req.limit if req.limit is not None else src.limit
            new_att = req.attachment if req.attachment is not None else src.attachment
            new_rol = req.rol if req.rol is not None else src.rol
            new_signed = req.signed_line if req.signed_line is not None else src.signed_line
            new_prem = round(new_limit * new_rol, 4)

            src_prof = ProgrammeProfile(**json.loads(src.profile_json)) if src.profile_json else ProgrammeProfile()
            new_prof = src_prof.model_copy(
                update={
                    "unique_code": f"RL-{next_year}-{new_id[-4:].upper()}",
                    "new_or_renewal": "RENEWAL",
                    "prior_year_limit_m": src.limit,
                    "prior_year_premium_m": src.deposit_premium,
                    "prior_year_rol_pct": round(src.rol * 100.0, 2),
                    "prior_year_signed_line": src.signed_line,
                    "rarc_total_pct": req.rarc_total_pct if req.rarc_total_pct is not None else src_prof.rarc_total_pct,
                }
            )

            src_uplift = UpliftSet(**json.loads(src.uplift_set_json))
            src_uplift.uplift_set_id = f"upl-{new_id}"
            src_uplift.version = 1

            src_graph = GraphSpec(**json.loads(src.graph_json))
            ensure_layer_codes(src_graph, strict=False)
            for node in src_graph.nodes:
                if node.node_type in _LAYER_TYPES and req.rarc_total_pct is not None:
                    node.params = {**node.params, "rarcPct": req.rarc_total_pct}
                if node.node_type == NodeType.PRICING_OUTPUT and node.params.get("contractId") == src.programme_id:
                    updated_params = {**node.params, "contractId": new_id}
                    if isinstance(updated_params.get("linkedLabel"), str) and src_prof.unique_code:
                        updated_params["linkedLabel"] = updated_params["linkedLabel"].replace(
                            src_prof.unique_code, new_prof.unique_code
                        )
                    node.params = updated_params
            src_graph.graph_id = f"grp-{new_id}"
            src_graph.version = 1
            src_graph.content_hash = canonical_hash({
                "nodes": [n.model_dump() for n in src_graph.nodes],
                "edges": [e.model_dump() for e in src_graph.edges],
            })

            base_name = src.programme_name.replace(f" ({src.treaty_year} Expiring)", "").strip()
            rec = ContractRecord(
                programme_id=new_id,
                chain_id=chain_id,
                prior_contract_id=src.programme_id,
                cedent_name=src.cedent_name,
                programme_name=base_name,
                treaty_year=next_year,
                renewal_period=next_period,
                office=src.office,
                underwriter=src.underwriter,
                secondary_underwriter=src.secondary_underwriter,
                broker=src.broker,
                fronting_carrier=src.fronting_carrier,
                collateral_cell=f"Cell-{next_period}",
                status=ProgrammeStatus.QUOTED.value,
                direction=src.direction,
                structure=src.structure,
                instrument=src.instrument,
                placement=src.placement,
            inception_date=f"{next_year}-01-01",
            expiry_date=f"{next_year}-12-31",
            portfolio_snapshot_date=(f"{next_year}-01-01" if src.portfolio_snapshot_date else None),
                hours_clause=src.hours_clause,
                written_line=new_signed,
                signed_line=new_signed,
                limit=new_limit,
                attachment=new_att,
                deposit_premium=new_prem,
                rol=new_rol,
                brokerage_pct=src.brokerage_pct,
                fronting_fee_pct=src.fronting_fee_pct,
                collateral_required=new_limit,
                currency=src.currency,
                sim_set_id=src.sim_set_id,
                exposure_eclipse_programme_id=src.exposure_eclipse_programme_id,
                region_perils_json=src.region_perils_json,
                perils_json=src.perils_json,
                custom_tags_json=src.custom_tags_json,
                uplift_set_json=src_uplift.model_dump_json(),
                graph_json=src_graph.model_dump_json(),
                ilw_spec_json=src.ilw_spec_json,
                profile_json=new_prof.model_dump_json(),
                version=1,
                content_hash=canonical_hash({"id": new_id, "prior": src.programme_id, "year": next_year}),
            )
            db.add(rec)
            db.commit()
            db.refresh(rec)
            out = _record_to_full(rec)

        lineage.add_link(new_id, programme_id, LinkType.SUPERSEDES)
        lineage.add_link(new_id, out.sim_set_id, LinkType.USES_LOSSES)
        lineage.add_link(new_id, out.graph.graph_id, LinkType.USES_STRUCTURE)
        return out

    def _chain_records(self, db, programme_id: str) -> list[ContractRecord]:
        """Programmes linked to this one by chain id or prior-year links. Never the whole book."""
        target = db.get(ContractRecord, programme_id)
        if not target:
            return []
        chain_recs: dict[str, ContractRecord] = {target.programme_id: target}
        if target.chain_id:
            for rec in db.query(ContractRecord).filter(ContractRecord.chain_id == target.chain_id).all():
                chain_recs[rec.programme_id] = rec
        changed = True
        while changed:
            changed = False
            for rec in list(chain_recs.values()):
                if rec.prior_contract_id and rec.prior_contract_id not in chain_recs:
                    prior = db.get(ContractRecord, rec.prior_contract_id)
                    if prior:
                        chain_recs[prior.programme_id] = prior
                        changed = True
            if chain_recs:
                for child in db.query(ContractRecord).filter(ContractRecord.prior_contract_id.in_(list(chain_recs.keys()))).all():
                    if child.programme_id not in chain_recs:
                        chain_recs[child.programme_id] = child
                        changed = True
        return sorted(chain_recs.values(), key=lambda rec: (rec.treaty_year, rec.programme_id))

    def related_programmes(self, programme_id: str) -> list[RelatedProgramme] | None:
        with get_session() as db:
            target = db.get(ContractRecord, programme_id)
            if not target:
                return None
            return [
                RelatedProgramme(
                    programme_id=rec.programme_id,
                    cedent_name=rec.cedent_name,
                    programme_name=rec.programme_name,
                    treaty_year=rec.treaty_year,
                    renewal_period=rec.renewal_period,
                    status=rec.status,
                    direction=rec.direction,
                    is_hedge=is_hedge_direction(rec.direction),
                    structure=rec.structure,
                )
                for rec in self._chain_records(db, programme_id)
            ]

    def bound_pricing_slots(self) -> list[PricingBinding]:
        """Pricing outputs that already have a CMS layer and an incoming edge, across programmes."""
        found: list[PricingBinding] = []
        with get_session() as db:
            rows = db.query(ContractRecord.programme_id, ContractRecord.graph_json).all()
            for programme_id, raw in rows:
                graph = GraphSpec.model_validate_json(raw)
                incoming = {edge.target for edge in graph.edges}
                for node in graph.nodes:
                    if node.node_type != NodeType.PRICING_OUTPUT or node.node_id not in incoming:
                        continue
                    contract_id = str(node.params.get("contractId") or "").strip()
                    layer_id = str(node.params.get("layerNodeId") or "").strip()
                    if contract_id and layer_id:
                        found.append(PricingBinding(
                            contract_id=contract_id,
                            layer_node_id=layer_id,
                            programme_id=programme_id,
                        ))
        return found

    def get_renewal_chain(self, programme_id: str) -> list[RenewalChainRow]:
        """Return all programmes in the YoY renewal chain ordered newest year first, with YoY deltas."""
        with get_session() as db:
            ordered_asc = self._chain_records(db, programme_id)
            if not ordered_asc:
                return []
            by_id = {r.programme_id: r for r in ordered_asc}

            rows: list[RenewalChainRow] = []
            for idx, r in enumerate(ordered_asc):
                prof = ProgrammeProfile(**json.loads(r.profile_json)) if r.profile_json else ProgrammeProfile()
                prior_rec = by_id.get(r.prior_contract_id) if r.prior_contract_id else (ordered_asc[idx - 1] if idx > 0 else None)
                yoy_lim = round(r.limit - prior_rec.limit, 2) if prior_rec else None
                yoy_rol = round((r.rol - prior_rec.rol) * 100.0, 2) if prior_rec else None
                yoy_prem = round(r.deposit_premium - prior_rec.deposit_premium, 2) if prior_rec else None
                rows.append(
                    RenewalChainRow(
                        programme_id=r.programme_id,
                        unique_code=prof.unique_code or r.programme_id,
                        programme_name=r.programme_name,
                        treaty_year=r.treaty_year,
                        renewal_period=r.renewal_period,
                        status=r.status,
                        prior_contract_id=r.prior_contract_id,
                        limit=r.limit,
                        attachment=r.attachment,
                        deposit_premium=r.deposit_premium,
                        rol_pct=round(r.rol * 100.0, 2),
                        signed_line=r.signed_line,
                        rarc_total_pct=prof.rarc_total_pct,
                        yoy_limit_delta_m=yoy_lim,
                        yoy_rol_delta_pts=yoy_rol,
                        yoy_premium_delta_m=yoy_prem,
                        is_current=(r.programme_id == programme_id),
                    )
                )
            return list(reversed(rows))

    def current_entity_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for c in self.list_contracts():
            hashes[c.programme_id] = c.content_hash
            hashes[c.uplift_set.uplift_set_id] = c.uplift_set.content_hash
            hashes[c.graph.graph_id] = c.graph.content_hash
        return hashes
