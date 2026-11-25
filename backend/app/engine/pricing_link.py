"""Pricing Output economics: contract-layer terms applied to the loss stream feeding the node."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.models.domain import PricingEconomics
from app.models.enums import NodeType

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
    technical_rol: float | None = None
    warnings: list[str] = field(default_factory=list)


def _layer_share_and_premium(node: Any) -> tuple[float, float]:
    p = node.params
    if node.node_type == NodeType.CASCADING_TOWER:
        subs = p.get("layers", []) or []
        share = float(subs[0].get("placedShare", 1.0)) if subs else 1.0
        return share, sum(float(l.get("depositPremium", 0) or 0) for l in subs)
    return float(p.get("placedShare", 1.0)), float(p.get("depositPremium", 0.0) or 0.0)


def _layer_limit_attachment(node: Any) -> tuple[float | None, float | None]:
    p = node.params
    keys = {
        NodeType.OCC_XL: ("occLimit", "occAttachment"),
        NodeType.AGG_XL: ("aggLimit", "aggAttachment"),
        NodeType.ILW_OPTION: ("limit", "trigger"),
    }.get(node.node_type)
    if not keys:
        return None, None
    lim, att = p.get(keys[0]), p.get(keys[1])
    return (float(lim) if lim is not None else None), (float(att) if att is not None else None)


def _ratio(num: float, den: float | None) -> float | None:
    return num / den if den else None


def _fmt_m(v: float | None) -> str:
    return "—" if v is None else f"{v:,.4g}m"


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
    brokerage = our_premium * link.brokerage_pct
    exp_loss = float(np.mean(ann)) * scale
    exp_rip = float(np.mean(rip)) * rip_scale
    income = our_premium + exp_rip
    profit = income - brokerage - exp_loss
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
        layer_node_id=link.layer_node_id,
        layer_label=link.layer_label,
        limit=link.limit,
        attachment=link.attachment,
        premium=round(link.premium, 6),
        written_line=link.written_line,
        signed_line=link.signed_line,
        our_limit=round(link.limit * link.signed_line, 6) if link.limit else None,
        our_premium=round(our_premium, 6),
        brokerage_pct=link.brokerage_pct,
        brokerage=round(brokerage, 6),
        net_premium=round(our_premium - brokerage, 6),
        expected_loss=round(exp_loss, 6),
        expected_rip=round(exp_rip, 6),
        expected_profit=round(profit, 6),
        loss_ratio=round(exp_loss / income, 6) if income > 0 else None,
        combined_ratio=round((exp_loss + brokerage) / income, 6) if income > 0 else None,
        rol=round(rol, 6) if rol is not None else None,
        loss_on_line=round(loss_on_line, 6) if loss_on_line is not None else None,
        multiple=round(rol / loss_on_line, 4) if rol and loss_on_line else None,
        tvar_250=round(our_tvar, 6),
        return_on_tvar=round(profit / our_tvar, 6) if our_tvar > 0 else None,
        technical_rol=link.technical_rol,
        price_adequacy=round(rol / link.technical_rol, 6) if rol and link.technical_rol else None,
        warnings=warnings,
    )
