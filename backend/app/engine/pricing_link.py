"""Pricing Output economics: contract-layer terms applied to the loss stream feeding the node."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.models.domain import PricingEconomics
from app.models.enums import ContractDirection, NodeType, is_hedge_direction

LAYER_NODE_TYPES = (
    NodeType.OCC_XL,
    NodeType.AGG_XL,
    NodeType.QUOTA_SHARE,
    NodeType.AGG_QUOTA_SHARE,
    NodeType.SURPLUS_SHARE,
    NodeType.ILW_OPTION,
    NodeType.CASCADING_TOWER,
)


@dataclass(slots=True)
class PricingLink:
    source: str
    contract_id: str
    contract_code: str
    contract_name: str
    layer_node_id: str
    layer_label: str
    limit: float | None
    attachment: float | None
    premium: float
    written_line: float
    signed_line: float
    brokerage_pct: float
    layer_code: str = ""
    direction: str = "ASSUMED"
    is_hedge: bool = False
    rolls_up: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RollupLayerEntry:
    """One contract layer selected for portfolio rollup, optimizer, or retro allocation."""

    programme_id: str
    cedent_name: str
    programme_name: str
    status: str
    region_perils: list[str]
    layer_id: str
    layer_code: str
    layer_name: str
    signed_line: float
    limit_1x: float
    prem_1x: float
    limit: float
    premium: float
    loss_vec: np.ndarray
    rip_vec: np.ndarray
    loss_vec_1x: np.ndarray
    rip_vec_1x: np.ndarray
    direction: str = "ASSUMED"
    is_hedge: bool = False
    pricing_node_id: str | None = None
    feeder_node_id: str | None = None


def _safe_float(raw: Any, default: float) -> float:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return default
    return float(raw)


def _opt_float(raw: Any) -> float | None:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None
    return float(raw)


def _layer_share_and_premium(node: Any, default_signed: float = 1.0) -> tuple[float, float]:
    p = node.params
    if node.node_type == NodeType.CASCADING_TOWER:
        subs = p.get("layers", []) or []
        share = _safe_float(subs[0].get("placedShare"), default_signed) if subs else default_signed
        return share, sum(_safe_float(l.get("depositPremium"), 0.0) for l in subs)
    return _safe_float(p.get("placedShare"), default_signed), _safe_float(p.get("depositPremium"), 0.0)


def _layer_limit_1x(node: Any, fallback_limit: float) -> float:
    p = node.params
    if node.node_type == NodeType.CASCADING_TOWER:
        subs = p.get("layers", []) or []
        total = sum(_safe_float(l.get("occLimit"), 0.0) for l in subs)
        return total if total > 0 else fallback_limit
    for key in ("occLimit", "aggLimit", "limit", "aggCap"):
        val = _opt_float(p.get(key))
        if val is not None:
            return val
    return fallback_limit


def _layer_limit_attachment(node: Any) -> tuple[float | None, float | None]:
    p = node.params
    keys = {
        NodeType.OCC_XL: ("occLimit", "occAttachment"),
        NodeType.AGG_XL: ("aggLimit", "aggAttachment"),
        NodeType.ILW_OPTION: ("limit", "trigger"),
    }.get(node.node_type)
    if not keys:
        return None, None
    return _opt_float(p.get(keys[0])), _opt_float(p.get(keys[1]))


def _ratio(num: float, den: float | None) -> float | None:
    return num / den if den else None


def _fmt_m(v: float | None) -> str:
    return "—" if v is None else f"{v:,.4g}m"


def graph_uses_explicit_pricing_links(graph: Any) -> bool:
    """True when a programme graph uses explicit 1:1 Pricing Output -> CMS layer links (or explicitly unlinked outputs)."""
    for node in graph.nodes:
        if node.node_type != NodeType.PRICING_OUTPUT:
            continue
        if str(node.params.get("contractId") or "").strip() or node.params.get("unlinked") is True:
            return True
    return False


def _feeder_metadata(in_edges: list[Any], nodes_by_id: dict[str, Any]) -> tuple[str | None, str, str]:
    if not in_edges:
        return None, "", ""
    if len(in_edges) == 1:
        src = nodes_by_id.get(in_edges[0].source)
        if src is None:
            return None, "", ""
        code = str(src.params.get("layerCode") or "").strip() if src.node_type in LAYER_NODE_TYPES else ""
        return src.node_id, code, src.label
    sources = [nodes_by_id[e.source] for e in in_edges if e.source in nodes_by_id]
    if not sources:
        return None, "", ""
    return sources[0].node_id, "", " + ".join(s.label for s in sources)


def compute_pricing_economics(
    link: PricingLink,
    in_edges: list[Any],
    nodes_by_id: dict[str, Any],
    ann: np.ndarray,
    rip: np.ndarray,
    tvar_250: float,
) -> PricingEconomics:
    """Losses arriving at the node are already at the upstream layer's placed share; restate them at
    100% and re-apply the linked contract's signed line so a different contract can be priced
    against the same modelled structure."""
    warnings = list(link.warnings)
    feeder_node_id, feeder_layer_code, feeder_label = _feeder_metadata(in_edges, nodes_by_id)
    upstream_share = 1.0
    upstream_premium: float | None = None
    layer_inputs = [e for e in in_edges if nodes_by_id[e.source].node_type in LAYER_NODE_TYPES]
    if len(in_edges) == 1 and layer_inputs:
        e = layer_inputs[0]
        src = nodes_by_id[e.source]
        share, upstream_premium = _layer_share_and_premium(src)
        upstream_share = share * e.sign * e.weight
        if link.source == "LINKED":
            lim, att = _layer_limit_attachment(src)
            if (link.limit is not None and lim is not None and abs(link.limit - lim) > 1e-6) or (
                link.attachment is not None and att is not None and abs(link.attachment - att) > 1e-6
            ):
                warnings.append(
                    f"Linked layer is {_fmt_m(link.limit)} xs {_fmt_m(link.attachment)} but the modelled layer is "
                    f"{_fmt_m(lim)} xs {_fmt_m(att)}; losses follow the graph."
                )
    elif len(in_edges) > 1:
        warnings.append("Several inputs feed this node; their combined loss is treated as 100% of the linked layer.")
    elif not in_edges:
        warnings.append("Nothing is connected to this node yet.")

    scale = link.signed_line / upstream_share if upstream_share > 0 else 0.0
    rip_scale = scale
    if upstream_premium and upstream_premium > 0:
        rip_scale *= link.premium / upstream_premium

    our_premium = link.premium * link.signed_line
    exp_loss = float(np.mean(ann)) * scale
    exp_rip = float(np.mean(rip)) * rip_scale
    income = our_premium + exp_rip
    if link.is_hedge:
        # Buying protection: we pay premium + RIP and receive expected recoveries (exp_loss)
        brokerage = 0.0
        net_premium = our_premium
        profit = exp_loss - our_premium - exp_rip
        combined_ratio = (exp_loss / income) if income > 0 else None
    else:
        brokerage = our_premium * link.brokerage_pct
        net_premium = our_premium - brokerage
        profit = income - brokerage - exp_loss
        combined_ratio = ((exp_loss + brokerage) / income) if income > 0 else None
    our_tvar = tvar_250 * scale
    rol = _ratio(link.premium, link.limit)
    loss_on_line = _ratio(exp_loss / link.signed_line, link.limit) if link.signed_line > 0 else None
    if link.premium <= 0:
        warnings.append("Linked layer has no premium, so ratios are unavailable.")

    return PricingEconomics(
        source=link.source,
        contract_id=link.contract_id,
        contract_code=link.contract_code,
        contract_name=link.contract_name,
        direction=link.direction,
        is_hedge=link.is_hedge,
        layer_node_id=link.layer_node_id,
        layer_code=link.layer_code,
        layer_label=link.layer_label,
        feeder_node_id=feeder_node_id,
        feeder_layer_code=feeder_layer_code,
        feeder_label=feeder_label,
        rolls_up=link.rolls_up,
        limit=link.limit,
        attachment=link.attachment,
        premium=round(link.premium, 6),
        written_line=link.written_line,
        signed_line=link.signed_line,
        our_limit=round(link.limit * link.signed_line, 6) if link.limit else None,
        our_premium=round(our_premium, 6),
        brokerage_pct=0.0 if link.is_hedge else link.brokerage_pct,
        brokerage=round(brokerage, 6),
        net_premium=round(net_premium, 6),
        expected_loss=round(exp_loss, 6),
        expected_rip=round(exp_rip, 6),
        expected_profit=round(profit, 6),
        loss_ratio=round(exp_loss / income, 6) if income > 0 else None,
        combined_ratio=round(combined_ratio, 6) if combined_ratio is not None else None,
        rol=round(rol, 6) if rol is not None else None,
        loss_on_line=round(loss_on_line, 6) if loss_on_line is not None else None,
        multiple=round(rol / loss_on_line, 4) if rol and loss_on_line else None,
        tvar_250=round(our_tvar, 6),
        return_on_tvar=round(profit / our_tvar, 6) if our_tvar > 0 else None,
        warnings=warnings,
    )


ROLLUP_TERM_TYPES = (
    NodeType.OCC_XL,
    NodeType.AGG_XL,
    NodeType.QUOTA_SHARE,
    NodeType.AGG_QUOTA_SHARE,
    NodeType.SURPLUS_SHARE,
    NodeType.ILW_OPTION,
    NodeType.CASCADING_TOWER,
)


def extract_rollup_layers(
    programmes: list[Any],
    ann_by_prog: dict[str, dict[str, np.ndarray]],
    rip_by_prog: dict[str, dict[str, np.ndarray]],
    *,
    all_programmes: dict[str, Any] | None = None,
) -> list[RollupLayerEntry]:
    """Select the layers that roll up into portfolio / optimizer / retro allocation.

    When a programme graph uses explicit 1:1 Pricing Output -> CMS layer links (or explicitly unlinked
    Pricing Outputs), ONLY the linked Pricing Outputs roll up: each takes its modelled loss stream from
    its feeder connection and applies the linked CMS contract layer's terms & signed line. Unlinked
    Pricing Outputs and exploratory canvas layers are excluded from rollups.

    Legacy graphs without any configured Pricing Output links fall back to rolling up their financial-terms
    nodes (skipping any layer already claimed by a linked Pricing Output on another programme).
    """
    catalog: dict[str, Any] = dict(all_programmes or {})
    for p in programmes:
        # Prefer the programme instance in `programmes` (preserves branch signed-line overrides).
        catalog[p.programme_id] = p

    entries: list[RollupLayerEntry] = []
    claimed_targets: set[tuple[str, str]] = set()
    legacy_progs: list[Any] = []

    # Pass 1: explicit 1:1 linked Pricing Outputs
    for prog in programmes:
        if not graph_uses_explicit_pricing_links(prog.graph):
            legacy_progs.append(prog)
            continue
        nodes_by_id = {n.node_id: n for n in prog.graph.nodes}
        ann_map = ann_by_prog[prog.programme_id]
        rip_map = rip_by_prog[prog.programme_id]

        for px in prog.graph.nodes:
            if px.node_type != NodeType.PRICING_OUTPUT:
                continue
            target_cid = str(px.params.get("contractId") or "").strip()
            if not target_cid:
                continue
            target_prog = catalog.get(target_cid)
            if target_prog is None:
                continue
            target_layers = [n for n in target_prog.graph.nodes if n.node_type in LAYER_NODE_TYPES]
            if not target_layers:
                continue
            wanted_lid = str(px.params.get("layerNodeId") or "").strip()
            target_layer = next((n for n in target_layers if n.node_id == wanted_lid), target_layers[0])
            target_key = (target_prog.programme_id, target_layer.node_id)
            if target_key in claimed_targets:
                continue
            claimed_targets.add(target_key)

            target_signed, target_prem_1x = _layer_share_and_premium(target_layer, target_prog.signed_line)
            target_limit_1x = _layer_limit_1x(target_layer, target_prog.limit)

            in_edges = [e for e in prog.graph.edges if e.target == px.node_id and e.source in nodes_by_id]
            feeder_id, _, _ = _feeder_metadata(in_edges, nodes_by_id)
            raw_ann = ann_map[px.node_id]
            raw_rip = rip_map[px.node_id]

            if not in_edges:
                loss_1x = np.zeros_like(raw_ann)
                rip_1x = np.zeros_like(raw_rip)
            else:
                upstream_share = 1.0
                upstream_premium: float | None = None
                layer_inputs = [e for e in in_edges if nodes_by_id[e.source].node_type in LAYER_NODE_TYPES]
                if len(in_edges) == 1 and layer_inputs:
                    e = layer_inputs[0]
                    share, upstream_premium = _layer_share_and_premium(nodes_by_id[e.source], prog.signed_line)
                    upstream_share = share * e.sign * e.weight
                inv_up = (1.0 / upstream_share) if upstream_share > 1e-12 else 0.0
                rip_mult = (target_prem_1x / upstream_premium) if (upstream_premium and upstream_premium > 0) else 1.0
                loss_1x = raw_ann * inv_up
                rip_1x = raw_rip * inv_up * rip_mult

            target_dir = getattr(target_prog, "direction", ContractDirection.ASSUMED)
            target_dir_val = target_dir.value if hasattr(target_dir, "value") else str(target_dir)
            target_is_hedge = is_hedge_direction(target_dir)
            entries.append(
                RollupLayerEntry(
                    programme_id=target_prog.programme_id,
                    cedent_name=target_prog.cedent_name,
                    programme_name=target_prog.programme_name,
                    status=getattr(target_prog.status, "value", str(target_prog.status)),
                    region_perils=list(target_prog.region_perils),
                    layer_id=target_layer.node_id,
                    layer_code=str(target_layer.params.get("layerCode") or "").strip(),
                    layer_name=target_layer.label,
                    signed_line=target_signed,
                    limit_1x=target_limit_1x,
                    prem_1x=target_prem_1x,
                    limit=target_limit_1x * target_signed,
                    premium=target_prem_1x * target_signed,
                    loss_vec=loss_1x * target_signed,
                    rip_vec=rip_1x * target_signed,
                    loss_vec_1x=loss_1x,
                    rip_vec_1x=rip_1x,
                    direction=target_dir_val,
                    is_hedge=target_is_hedge,
                    pricing_node_id=px.node_id,
                    feeder_node_id=feeder_id,
                )
            )

    # Pass 2: legacy fallback for graphs with no configured Pricing Output links
    for prog in legacy_progs:
        ann_map = ann_by_prog[prog.programme_id]
        rip_map = rip_by_prog[prog.programme_id]
        prog_dir = getattr(prog, "direction", ContractDirection.ASSUMED)
        prog_dir_val = prog_dir.value if hasattr(prog_dir, "value") else str(prog_dir)
        prog_is_hedge = is_hedge_direction(prog_dir)
        for node in prog.graph.nodes:
            if node.node_type not in ROLLUP_TERM_TYPES:
                continue
            if (prog.programme_id, node.node_id) in claimed_targets:
                continue
            placed, prem_1x = _layer_share_and_premium(node, prog.signed_line)
            lim_1x = _layer_limit_1x(node, prog.limit)
            inv_placed = 1.0 / max(placed, 1e-6)
            loss_vec = ann_map[node.node_id]
            rip_vec = rip_map[node.node_id]
            entries.append(
                RollupLayerEntry(
                    programme_id=prog.programme_id,
                    cedent_name=prog.cedent_name,
                    programme_name=prog.programme_name,
                    status=getattr(prog.status, "value", str(prog.status)),
                    region_perils=list(prog.region_perils),
                    layer_id=node.node_id,
                    layer_code=str(node.params.get("layerCode") or "").strip(),
                    layer_name=node.label,
                    signed_line=placed,
                    limit_1x=lim_1x,
                    prem_1x=prem_1x,
                    limit=lim_1x * placed,
                    premium=prem_1x * placed,
                    loss_vec=loss_vec,
                    rip_vec=rip_vec,
                    loss_vec_1x=loss_vec * inv_placed,
                    rip_vec_1x=rip_vec * inv_placed,
                    direction=prog_dir_val,
                    is_hedge=prog_is_hedge,
                    pricing_node_id=None,
                    feeder_node_id=node.node_id,
                )
            )

    return entries
