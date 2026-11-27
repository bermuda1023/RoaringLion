"""Phase 0 + Phase 2 Engine — wraps the compiled Rust kernel (`roaring_lion_engine`).

Implements:
- Per-deal Uplifts (UNADJUSTED vs ADJUSTED)
- Full DAG evaluation across both views with content-addressed node caching:
  LOSS_SOURCE, INDUSTRY_INDEX_SOURCE, REGION_PERIL_FILTER, DATE_WINDOW_FILTER,
  LOSS_SIZE_FILTER, EVENT_ORDER_FILTER, COMBINATION (+/- weighted), UPLIFT,
  OCC_XL, AGG_XL, QUOTA_SHARE, AGG_QUOTA_SHARE, SURPLUS_SHARE, ILW_OPTION,
  CASCADING_TOWER, PRICING_OUTPUT, YELT_OUTPUT, EP_OUTPUT.
- Portfolio Rollup + ROC/IRR CDF + Risk Appetite Guidelines + Euler Co-TVaR allocation
- Deterministic loss runner calling the exact same Rust financial-terms code
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import numpy as np
import roaring_lion_engine as rle

from app.engine.pricing_link import (
    LAYER_NODE_TYPES,
    PricingLink,
    compute_pricing_economics,
    deployed_capital_m,
    extract_rollup_layers,
)
from app.engine.sim_generator import RP_TO_ID, YeltTable
from app.models.domain import (
    BreadcrumbStep,
    CdfPoint,
    DagEvalResponse,
    DeterministicLayerStatement,
    DeterministicRunRequest,
    DeterministicRunResponse,
    DeterministicStepResult,
    EpPoint,
    GraphSpec,
    GuidelineCheck,
    HedgeAccretionSummary,
    LayerAllocationRow,
    LayerStateSnapshot,
    NodeViewMetrics,
    PortfolioHedgeComparisonResponse,
    PortfolioRollupResponse,
    ProgrammeDeal,
    UpliftSet,
)
from app.models.enums import NodeType, ViewOfRisk, is_hedge_direction
from app.services.lineage import LineageRegistry, canonical_hash
from app.services.portfolio_snapshot import compute_unl_portfolio_events, is_ilw_programme
from app.services.sim_sets import _trim_memory

DEFAULT_RETURN_PERIODS = [10.0, 20.0, 50.0, 100.0, 200.0, 250.0, 500.0, 1000.0]

# Content-addressed LRU cache for DAG intermediate node outputs:
# key -> (ev_out, ann, mx, rip, ep_dict)
_NODE_CACHE: OrderedDict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]] = OrderedDict()
_NODE_CACHE_LOCK = threading.Lock()
_MAX_CACHE_ENTRIES = 16


def clear_dag_cache() -> None:
    with _NODE_CACHE_LOCK:
        _NODE_CACHE.clear()


def _cache_get(key: str):
    with _NODE_CACHE_LOCK:
        if key in _NODE_CACHE:
            _NODE_CACHE.move_to_end(key)
            return _NODE_CACHE[key]
        return None


def _cache_put(key: str, val) -> None:
    with _NODE_CACHE_LOCK:
        _NODE_CACHE[key] = val
        _NODE_CACHE.move_to_end(key)
        while len(_NODE_CACHE) > _MAX_CACHE_ENTRIES:
            _NODE_CACHE.popitem(last=False)


def _serialize_uplift_rules(uplift_set: UpliftSet) -> str:
    payload = []
    for r in uplift_set.rules:
        rp_id = -1 if r.region_peril_key == "ALL" else RP_TO_ID.get(r.region_peril_key, -999)
        payload.append({
            "region_peril_id": rp_id,
            "flat_severity_factor": r.flat_severity_factor,
            "curve_points": [
                {"loss_threshold": cp.loss_threshold, "factor": cp.factor}
                for cp in sorted(r.curve_points, key=lambda c: c.loss_threshold)
            ],
            "additive_load": r.additive_load,
            "min_loss": r.min_loss,
            "max_loss": r.max_loss,
        })
    return json.dumps(payload)


def apply_deal_view(yelt: YeltTable, uplift_set: UpliftSet, view: ViewOfRisk) -> np.ndarray:
    if view == ViewOfRisk.UNADJUSTED or not uplift_set.rules:
        return yelt.losses
    rules_json = _serialize_uplift_rules(uplift_set)
    return rle.apply_uplifts_yelt(yelt.losses, yelt.rp_ids, rules_json)


def _ep_stats_from_vector(vec: np.ndarray) -> dict[str, Any]:
    return json.loads(rle.ep_stats(np.ascontiguousarray(vec, dtype=np.float64), DEFAULT_RETURN_PERIODS))


def _check_finite_numbers(value: Any, path: str) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not np.isfinite(float(value)):
            raise ValueError(f"{path} must be a finite number")
    elif isinstance(value, dict):
        for k, v in value.items():
            _check_finite_numbers(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check_finite_numbers(v, f"{path}[{i}]")


def _is_blank_param(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


def _opt_float(raw: Any, default: float = 0.0) -> float:
    if _is_blank_param(raw):
        return default
    return float(raw)


def _opt_nullable_float(node: Any, key: str) -> float | None:
    raw = node.params.get(key)
    if _is_blank_param(raw):
        return None
    if isinstance(raw, bool):
        raise ValueError(f"Node {node.node_id} param '{key}' must be numeric")
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Node {node.node_id} param '{key}' must be numeric") from exc
    if not np.isfinite(val):
        raise ValueError(f"Node {node.node_id} param '{key}' must be finite")
    return val


def _num(node: Any, key: str, default: float | None = None) -> float:
    raw = node.params.get(key, default)
    if _is_blank_param(raw):
        if default is not None:
            return float(default)
        raise ValueError(f"Node {node.node_id} ({node.node_type.value}) requires numeric param '{key}'")
    if isinstance(raw, bool):
        raise ValueError(f"Node {node.node_id} param '{key}' must be numeric")
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Node {node.node_id} param '{key}' must be numeric") from exc
    if not np.isfinite(val):
        raise ValueError(f"Node {node.node_id} param '{key}' must be finite")
    return val


def _allowed_rp_keys_for_filter(params: dict[str, Any]) -> set[str] | None:
    """Resolve allowed region-peril keys from zones, perils, and/or explicit regionPerils.
    Returns None when unfiltered (pass all)."""
    explicit = [k for k in (params.get("regionPerils") or []) if k]
    zones = [z for z in (params.get("zones") or []) if z]
    perils = [p for p in (params.get("perils") or []) if p]

    if "ALL" in explicit and not zones and not perils:
        return None
    if not explicit and not zones and not perils:
        return None

    all_keys = list(RP_TO_ID.keys())
    matched: set[str] = set()

    if explicit and "ALL" not in explicit:
        matched.update(k for k in explicit if k in RP_TO_ID)

    if zones or perils:
        zp_set = set(all_keys)
        if zones and "ALL" not in zones:
            z_set = set(zones)
            zp_set = {
                k for k in zp_set
                if k.rsplit("-", 1)[0] in z_set or k.split("-")[0] in z_set
            }
        if perils and "ALL" not in perils:
            p_set = set(perils)
            zp_set = {k for k in zp_set if k.rsplit("-", 1)[-1] in p_set}
        matched.update(zp_set)

    return matched


def _validate_node_params(node: Any) -> None:
    _check_finite_numbers(node.params, f"Node {node.node_id} params")
    t = node.node_type
    placed_keys = ("placedShare",)
    if t == NodeType.OCC_XL:
        if _num(node, "occLimit") <= 0:
            raise ValueError(f"Node {node.node_id} occLimit must be > 0")
        if _num(node, "occAttachment") < 0:
            raise ValueError(f"Node {node.node_id} occAttachment must be >= 0")
        if _num(node, "occFranchise", 0.0) < 0:
            raise ValueError(f"Node {node.node_id} occFranchise must be >= 0")
        if _num(node, "depositPremium", 0.0) < 0:
            raise ValueError(f"Node {node.node_id} depositPremium must be >= 0")
        rip = _num(node, "ripRate", 0.0)
        if not 0.0 <= rip <= 5.0:
            raise ValueError(f"Node {node.node_id} ripRate must be between 0 and 5")
        reinst = _opt_nullable_float(node, "reinstatements")
        if reinst is not None and reinst < 0:
            raise ValueError(f"Node {node.node_id} reinstatements must be >= 0")
    elif t == NodeType.AGG_XL:
        if _num(node, "aggLimit") <= 0:
            raise ValueError(f"Node {node.node_id} aggLimit must be > 0")
        if _num(node, "aggAttachment") < 0:
            raise ValueError(f"Node {node.node_id} aggAttachment must be >= 0")
        if _num(node, "perEventFranchise", 0.0) < 0:
            raise ValueError(f"Node {node.node_id} perEventFranchise must be >= 0")
        if _num(node, "depositPremium", 0.0) < 0:
            raise ValueError(f"Node {node.node_id} depositPremium must be >= 0")
        cap = _opt_nullable_float(node, "perEventCap")
        if cap is not None and cap <= 0:
            raise ValueError(f"Node {node.node_id} perEventCap must be > 0")
    elif t in (NodeType.QUOTA_SHARE, NodeType.AGG_QUOTA_SHARE):
        cession = _num(node, "cessionShare", 0.25)
        if not 0.0 <= cession <= 1.0:
            raise ValueError(f"Node {node.node_id} cessionShare must be between 0 and 1")
        if _num(node, "depositPremium", 0.0) < 0:
            raise ValueError(f"Node {node.node_id} depositPremium must be >= 0")
        for key in ("eventCap", "aggCap"):
            v = _opt_nullable_float(node, key)
            if v is not None and v <= 0:
                raise ValueError(f"Node {node.node_id} {key} must be > 0")
    elif t == NodeType.SURPLUS_SHARE:
        if _num(node, "retentionLine", 25.0) <= 0:
            raise ValueError(f"Node {node.node_id} retentionLine must be > 0")
        lines = _num(node, "numLines", 4.0)
        if not 0.0 < lines <= 100.0:
            raise ValueError(f"Node {node.node_id} numLines must be between 0 and 100")
        if _num(node, "depositPremium", 0.0) < 0:
            raise ValueError(f"Node {node.node_id} depositPremium must be >= 0")
    elif t == NodeType.ILW_OPTION:
        trig = _num(node, "trigger", 40.0)
        if trig < 0:
            raise ValueError(f"Node {node.node_id} trigger must be >= 0")
        if _num(node, "limit", 25.0) <= 0:
            raise ValueError(f"Node {node.node_id} limit must be > 0")
        if _num(node, "depositPremium", 0.0) < 0:
            raise ValueError(f"Node {node.node_id} depositPremium must be >= 0")
        exh = _opt_nullable_float(node, "exhaustion")
        if exh is not None and exh < trig:
            raise ValueError(f"Node {node.node_id} exhaustion must be >= trigger")
    elif t == NodeType.CASCADING_TOWER:
        layers = node.params.get("layers") or []
        if not layers:
            raise ValueError(f"Node {node.node_id} cascading tower needs at least one layer")
        for i, layer in enumerate(layers):
            if _opt_float(layer.get("occLimit"), 0.0) <= 0:
                raise ValueError(f"Node {node.node_id} layer {i} occLimit must be > 0")
            if _opt_float(layer.get("occAttachment"), 0.0) < 0:
                raise ValueError(f"Node {node.node_id} layer {i} occAttachment must be >= 0")
            if _opt_float(layer.get("depositPremium"), 0.0) < 0:
                raise ValueError(f"Node {node.node_id} layer {i} depositPremium must be >= 0")
    elif t == NodeType.EVENT_ORDER_FILTER:
        min_order = _num(node, "minOrder", 2.0)
        if min_order < 1:
            raise ValueError(f"Node {node.node_id} minOrder must be >= 1")
        max_order = _opt_nullable_float(node, "maxOrder")
        if max_order is not None and max_order < min_order:
            raise ValueError(f"Node {node.node_id} maxOrder must be >= minOrder")
        if _num(node, "minLossThreshold", 0.0) < 0:
            raise ValueError(f"Node {node.node_id} minLossThreshold must be >= 0")
    elif t == NodeType.DATE_WINDOW_FILTER:
        for key in ("startDay", "endDay"):
            v = _num(node, key, 1.0 if key == "startDay" else 365.0)
            if not 1 <= v <= 365:
                raise ValueError(f"Node {node.node_id} {key} must be between 1 and 365")
    elif t == NodeType.LOSS_SIZE_FILTER:
        min_l = _num(node, "minLoss", 0.0)
        if min_l < 0:
            raise ValueError(f"Node {node.node_id} minLoss must be >= 0")
        max_l = _opt_nullable_float(node, "maxLoss")
        if max_l is not None and max_l < min_l:
            raise ValueError(f"Node {node.node_id} maxLoss must be >= minLoss")
    elif t == NodeType.REGION_PERIL_FILTER:
        for key in node.params.get("regionPerils", []) or []:
            if key != "ALL" and key not in RP_TO_ID:
                raise ValueError(f"Node {node.node_id} references unknown region-peril '{key}'")
        valid_zones = {k.rsplit("-", 1)[0] for k in RP_TO_ID} | {k.split("-")[0] for k in RP_TO_ID}
        for z in node.params.get("zones", []) or []:
            if z != "ALL" and z not in valid_zones:
                raise ValueError(f"Node {node.node_id} references unknown zone '{z}'")
        valid_perils = {k.rsplit("-", 1)[-1] for k in RP_TO_ID}
        for p in node.params.get("perils", []) or []:
            if p != "ALL" and p not in valid_perils:
                raise ValueError(f"Node {node.node_id} references unknown peril '{p}'")
    elif t == NodeType.UPLIFT:
        if _num(node, "factor", 1.0) < 0:
            raise ValueError(f"Node {node.node_id} uplift factor must be >= 0")
    for key in placed_keys:
        if key in node.params:
            share = _num(node, key, 1.0)
            if not 0.0 <= share <= 1.5:
                raise ValueError(f"Node {node.node_id} placedShare must be between 0 and 1.5")


def validate_dag_acyclic(graph: GraphSpec) -> list[str]:
    """Validate DAG structure, term parameters, acyclicity; return topological order."""
    node_ids = [n.node_id for n in graph.nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("Graph contains duplicate node ids")
    edge_ids = [e.edge_id for e in graph.edges]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("Graph contains duplicate edge ids")
    for node in graph.nodes:
        _validate_node_params(node)
    nodes_by_id = {n.node_id: n for n in graph.nodes}
    in_degree: dict[str, int] = {n.node_id: 0 for n in graph.nodes}
    outgoing: dict[str, list[str]] = {n.node_id: [] for n in graph.nodes}

    for edge in graph.edges:
        if edge.source not in nodes_by_id or edge.target not in nodes_by_id:
            raise ValueError(f"Edge {edge.edge_id} references unknown node")
        outgoing[edge.source].append(edge.target)
        in_degree[edge.target] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    topo: list[str] = []
    while queue:
        cur = queue.pop(0)
        topo.append(cur)
        for nxt in outgoing[cur]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    if len(topo) != len(nodes_by_id):
        raise ValueError("Graph contains a cycle")
    return topo


def evaluate_programme_dag(
    programme: ProgrammeDeal,
    yelt: YeltTable,
    lineage: LineageRegistry,
    views: list[ViewOfRisk] | None = None,
    industry_yelt: YeltTable | None = None,
    pricing_links: dict[str, PricingLink] | None = None,
    portfolio_unl_resolver: Callable[[str | None, ViewOfRisk], tuple[np.ndarray, str]] | None = None,
    *,
    compute_metrics: bool = True,
    need_events: bool = True,
) -> tuple[DagEvalResponse, dict[str, dict[str, np.ndarray]]]:
    """Evaluate a programme's DAG on UNADJUSTED and ADJUSTED views via Rust with content-addressed node caching."""
    t0 = time.perf_counter()
    target_views = views or [ViewOfRisk.UNADJUSTED, ViewOfRisk.ADJUSTED]
    nodes_by_id = {n.node_id: n for n in programme.graph.nodes}
    incoming: dict[str, list] = {n.node_id: [] for n in programme.graph.nodes}
    out_counts: dict[str, int] = {n.node_id: 0 for n in programme.graph.nodes}
    for edge in programme.graph.edges:
        incoming[edge.target].append(edge)
        out_counts[edge.source] += 1

    topo = validate_dag_acyclic(programme.graph)
    _PASS_THROUGH_TYPES = (
        NodeType.COMBINATION,
        NodeType.PRICING_OUTPUT,
        NodeType.YELT_OUTPUT,
        NodeType.EP_OUTPUT,
    )
    _ZERO_TRIALS = np.zeros(yelt.num_trials, dtype=np.float64)

    views_output: dict[str, list[NodeViewMetrics]] = {}
    annual_vectors: dict[str, dict[str, np.ndarray]] = {}
    rip_vectors: dict[str, dict[str, np.ndarray]] = {}
    event_vectors: dict[str, dict[str, np.ndarray]] = {}
    cache_hits = 0
    cache_misses = 0
    used_industry_yelt = False
    used_snapshots: dict[str, str] = {}

    for view in target_views:
        view_key = view.value
        base_losses = apply_deal_view(yelt, programme.uplift_set, view)
        view_source_hash = canonical_hash({
            "sim_set": yelt.content_hash,
            "view": view_key,
            "uplift": programme.uplift_set.content_hash if view == ViewOfRisk.ADJUSTED else "UNADJ",
        })

        node_event_streams: dict[str, np.ndarray] = {}
        node_ann: dict[str, np.ndarray] = {}
        node_mx: dict[str, np.ndarray] = {}
        node_ep: dict[str, dict[str, Any]] = {}
        node_rip: dict[str, np.ndarray] = {}
        node_hashes: dict[str, str] = {}
        node_metrics: list[NodeViewMetrics] = []
        resolved_snapshots: dict[str, tuple[np.ndarray, str]] = {}
        remaining_uses = dict(out_counts)

        def _ann_and_mx(ev: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            if not compute_metrics:
                return _ZERO_TRIALS, _ZERO_TRIALS
            ann_v = np.bincount(yelt.trial_ids - 1, weights=ev, minlength=yelt.num_trials)
            mx_v = np.zeros(yelt.num_trials, dtype=np.float64)
            np.maximum.at(mx_v, yelt.trial_ids - 1, ev)
            return ann_v, mx_v

        for nid in topo:
            node = nodes_by_id[nid]
            in_edges = incoming[nid]
            upstream_sig = [
                (node_hashes[e.source], e.sign, e.weight)
                for e in sorted(in_edges, key=lambda x: (x.source, x.edge_id))
            ]
            snap_hash = ""
            if node.node_type == NodeType.INDUSTRY_INDEX_SOURCE and industry_yelt is not None:
                used_industry_yelt = True
            if node.node_type == NodeType.PORTFOLIO_ROLLUP and portfolio_unl_resolver is not None:
                as_of = str(node.params.get("asOfDate") or programme.portfolio_snapshot_date or "").strip() or None
                snap_cache_key = as_of or ""
                if snap_cache_key not in resolved_snapshots:
                    resolved_snapshots[snap_cache_key] = portfolio_unl_resolver(as_of, view)
                snap_hash = resolved_snapshots[snap_cache_key][1]
                used_snapshots[as_of or "current"] = snap_hash
            node_key = canonical_hash({
                "src": view_source_hash if not in_edges else "",
                "ind": industry_yelt.content_hash if (industry_yelt and node.node_type == NodeType.INDUSTRY_INDEX_SOURCE) else "",
                "snap": snap_hash,
                "upstream": upstream_sig,
                "type": node.node_type.value,
                "params": node.params,
            })
            node_hashes[nid] = node_key

            single_linear = (
                len(in_edges) == 1
                and in_edges[0].sign == 1
                and abs(in_edges[0].weight - 1.0) < 1e-12
            )

            cached = _cache_get(node_key)
            if cached is not None:
                ev_out, ann, mx, rip, ep = cached
                cache_hits += 1
            elif single_linear and node.node_type in _PASS_THROUGH_TYPES:
                # Zero-copy pass-through for 1:1 linear output/combination edges
                src_id = in_edges[0].source
                ev_out = node_event_streams[src_id]
                ann = node_ann[src_id]
                if not compute_metrics and ann is _ZERO_TRIALS:
                    ann = np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials)
                mx = node_mx.get(src_id, _ZERO_TRIALS)
                rip = node_rip.get(src_id, _ZERO_TRIALS)
                ep = node_ep.get(src_id) if compute_metrics else None
                if compute_metrics:
                    if ep is None:
                        ep = _ep_stats_from_vector(ann)
                    _cache_put(node_key, (ev_out, ann, mx, rip, ep))
                cache_misses += 1
            else:
                cache_misses += 1
                if single_linear:
                    combined = node_event_streams[in_edges[0].source]
                elif in_edges:
                    combined = np.zeros_like(base_losses)
                    for e in in_edges:
                        combined += (e.sign * e.weight) * node_event_streams[e.source]
                    combined = np.maximum(combined, 0.0)
                else:
                    combined = base_losses

                rip = _ZERO_TRIALS

                if node.node_type == NodeType.LOSS_SOURCE:
                    ev_out = base_losses
                    ann, mx = _ann_and_mx(ev_out)
                elif node.node_type == NodeType.INDUSTRY_INDEX_SOURCE:
                    if industry_yelt is not None and len(industry_yelt.losses) == len(yelt.losses):
                        ev_out = apply_deal_view(industry_yelt, programme.uplift_set, view)
                    else:
                        ev_out = base_losses
                    ann, mx = _ann_and_mx(ev_out)
                elif node.node_type == NodeType.PORTFOLIO_ROLLUP:
                    if portfolio_unl_resolver is not None:
                        as_of = str(node.params.get("asOfDate") or programme.portfolio_snapshot_date or "").strip() or None
                        ev_out = resolved_snapshots[as_of or ""][0]
                    else:
                        ev_out = combined
                    ann, mx = _ann_and_mx(ev_out)
                elif node.node_type == NodeType.REGION_PERIL_FILTER:
                    allowed = _allowed_rp_keys_for_filter(node.params)
                    if allowed is None:
                        ev_out = combined
                    else:
                        allowed_ids = [RP_TO_ID[k] for k in allowed if k in RP_TO_ID]
                        mask = np.isin(yelt.rp_ids, allowed_ids)
                        ev_out = np.where(mask, combined, 0.0)
                    ann, mx = _ann_and_mx(ev_out)
                elif node.node_type == NodeType.DATE_WINDOW_FILTER:
                    start_day = int(_opt_float(node.params.get("startDay"), 1.0))
                    end_day = int(_opt_float(node.params.get("endDay"), 365.0))
                    if start_day <= end_day:
                        mask = (yelt.day_of_year >= start_day) & (yelt.day_of_year <= end_day)
                    else:
                        mask = (yelt.day_of_year >= start_day) | (yelt.day_of_year <= end_day)
                    ev_out = np.where(mask, combined, 0.0)
                    ann, mx = _ann_and_mx(ev_out)
                elif node.node_type == NodeType.LOSS_SIZE_FILTER:
                    min_l = _opt_float(node.params.get("minLoss"), 0.0)
                    max_l = _opt_nullable_float(node, "maxLoss")
                    mask = combined >= min_l
                    if max_l is not None:
                        mask &= combined <= max_l
                    ev_out = np.where(mask, combined, 0.0)
                    ann, mx = _ann_and_mx(ev_out)
                elif node.node_type == NodeType.EVENT_ORDER_FILTER:
                    max_ord = _opt_nullable_float(node, "maxOrder")
                    eo_json = json.dumps({
                        "min_order": int(_opt_float(node.params.get("minOrder"), 2.0)),
                        "max_order": int(max_ord) if max_ord is not None else None,
                        "min_loss_threshold": _opt_float(node.params.get("minLossThreshold"), 0.0),
                    })
                    ev_out = rle.eval_event_order_filter_yelt(
                        yelt.trial_ids,
                        np.ascontiguousarray(combined, dtype=np.float64),
                        yelt.num_trials,
                        eo_json,
                    )
                    ann, mx = _ann_and_mx(ev_out)
                elif node.node_type == NodeType.UPLIFT:
                    factor = _opt_float(node.params.get("factor"), 1.0)
                    add_load = _opt_float(node.params.get("additiveLoad"), 0.0)
                    ev_out = np.where(combined > 0.0, np.maximum(combined * factor + add_load, 0.0), 0.0)
                    ann, mx = _ann_and_mx(ev_out)
                elif node.node_type == NodeType.OCC_XL:
                    p_json = json.dumps({
                        "occ_limit": float(node.params["occLimit"]),
                        "occ_attachment": float(node.params["occAttachment"]),
                        "occ_franchise": _opt_float(node.params.get("occFranchise"), 0.0),
                        "reinstatements": _opt_nullable_float(node, "reinstatements"),
                        "rip_rate": _opt_float(node.params.get("ripRate"), 0.0),
                        "deposit_premium": _opt_float(node.params.get("depositPremium"), 0.0),
                        "placed_share": _opt_float(node.params.get("placedShare"), 1.0),
                    })
                    ev_out, ann, mx, rip = rle.eval_occ_xl_yelt(
                        yelt.trial_ids,
                        np.ascontiguousarray(combined, dtype=np.float64),
                        yelt.num_trials,
                        p_json,
                    )
                elif node.node_type == NodeType.AGG_XL:
                    p_json = json.dumps({
                        "agg_limit": float(node.params["aggLimit"]),
                        "agg_attachment": float(node.params["aggAttachment"]),
                        "per_event_franchise": _opt_float(node.params.get("perEventFranchise"), 0.0),
                        "per_event_cap": _opt_nullable_float(node, "perEventCap"),
                        "placed_share": _opt_float(node.params.get("placedShare"), 1.0),
                    })
                    ev_out, ann, mx = rle.eval_agg_xl_yelt(
                        yelt.trial_ids,
                        np.ascontiguousarray(combined, dtype=np.float64),
                        yelt.num_trials,
                        p_json,
                    )
                elif node.node_type in (NodeType.QUOTA_SHARE, NodeType.AGG_QUOTA_SHARE):
                    p_json = json.dumps({
                        "cession_share": _opt_float(node.params.get("cessionShare"), 0.25),
                        "event_cap": _opt_nullable_float(node, "eventCap"),
                        "agg_cap": _opt_nullable_float(node, "aggCap"),
                        "placed_share": _opt_float(node.params.get("placedShare"), 1.0),
                    })
                    ev_out, ann, mx = rle.eval_quota_share_yelt(
                        yelt.trial_ids,
                        np.ascontiguousarray(combined, dtype=np.float64),
                        yelt.num_trials,
                        p_json,
                    )
                elif node.node_type == NodeType.SURPLUS_SHARE:
                    p_json = json.dumps({
                        "retention_line": _opt_float(node.params.get("retentionLine"), 25.0),
                        "num_lines": _opt_float(node.params.get("numLines"), 4.0),
                        "placed_share": _opt_float(node.params.get("placedShare"), 1.0),
                    })
                    ev_out, ann, mx = rle.eval_surplus_share_yelt(
                        yelt.trial_ids,
                        np.ascontiguousarray(combined, dtype=np.float64),
                        yelt.num_trials,
                        p_json,
                    )
                elif node.node_type == NodeType.ILW_OPTION:
                    trig = _opt_float(node.params.get("trigger"), 40.0)
                    lim = _opt_float(node.params.get("limit"), 25.0)
                    placed = _opt_float(node.params.get("placedShare"), 1.0)
                    raw_exh = _opt_nullable_float(node, "exhaustion")
                    exh = raw_exh if (raw_exh is not None and raw_exh > trig) else trig
                    trig_type = str(node.params.get("triggerType") or "BINARY").upper()
                    cap = lim * placed
                    trial_idx = yelt.trial_ids - 1
                    if trig_type == "PRO_RATA" and exh > trig:
                        raw_ev = np.clip((combined - trig) / (exh - trig), 0.0, 1.0) * cap
                        mx = np.zeros(yelt.num_trials, dtype=np.float64)
                        np.maximum.at(mx, trial_idx, raw_ev)
                        ev_out = np.zeros_like(combined)
                        at_max = (raw_ev > 0.0) & (raw_ev >= mx[trial_idx] - 1e-12)
                        hit_pos = np.flatnonzero(at_max)
                        if len(hit_pos):
                            _, first_idx = np.unique(trial_idx[hit_pos], return_index=True)
                            first_pos = hit_pos[first_idx]
                            ev_out[first_pos] = mx[trial_idx[first_pos]]
                        ann = mx.copy()
                    elif trig_type == "AGGREGATE":
                        ann_ind = np.bincount(trial_idx, weights=combined, minlength=yelt.num_trials)
                        if exh > trig:
                            ann = np.clip((ann_ind - trig) / (exh - trig), 0.0, 1.0) * cap
                        else:
                            ann = np.where(ann_ind >= trig, cap, 0.0)
                        trial_sum = ann_ind[trial_idx]
                        ev_out = np.where(
                            trial_sum > 1e-12,
                            ann[trial_idx] * (combined / np.maximum(trial_sum, 1e-12)),
                            0.0,
                        )
                        if compute_metrics:
                            mx = np.zeros(yelt.num_trials, dtype=np.float64)
                            np.maximum.at(mx, trial_idx, ev_out)
                        else:
                            mx = _ZERO_TRIALS
                    elif trig_type == "SECOND_EVENT":
                        exceed = combined >= trig
                        counts = np.bincount(trial_idx, weights=exceed.astype(np.float64), minlength=yelt.num_trials)
                        hit_trial = counts >= 2.0
                        ann = np.where(hit_trial, cap, 0.0)
                        ev_out = np.where(
                            exceed & hit_trial[trial_idx],
                            cap / np.maximum(counts[trial_idx], 1.0),
                            0.0,
                        )
                        if compute_metrics:
                            mx = np.zeros(yelt.num_trials, dtype=np.float64)
                            np.maximum.at(mx, trial_idx, ev_out)
                        else:
                            mx = _ZERO_TRIALS
                    else:
                        ev_out = np.zeros_like(combined)
                        hit_pos = np.flatnonzero(combined >= trig)
                        if len(hit_pos):
                            _, first_idx = np.unique(trial_idx[hit_pos], return_index=True)
                            ev_out[hit_pos[first_idx]] = cap
                        mx = np.zeros(yelt.num_trials, dtype=np.float64)
                        np.maximum.at(mx, trial_idx, ev_out)
                        ann = mx.copy()
                elif node.node_type == NodeType.CASCADING_TOWER:
                    raw_layers = node.params.get("layers", [])
                    layers_json = json.dumps([
                        {
                            "occ_limit": float(l["occLimit"]),
                            "occ_attachment": float(l["occAttachment"]),
                            "occ_franchise": _opt_float(l.get("occFranchise"), 0.0),
                            "reinstatements": (
                                0.0
                                if "reinstatements" not in l
                                else (None if _is_blank_param(l.get("reinstatements")) else float(l["reinstatements"]))
                            ),
                            "rip_rate": _opt_float(l.get("ripRate"), 0.0),
                            "deposit_premium": _opt_float(l.get("depositPremium"), 0.0),
                            "placed_share": _opt_float(l.get("placedShare"), 1.0),
                        }
                        for l in raw_layers
                    ])
                    ev_out, ann, mx, rip = rle.eval_cascading_yelt(
                        yelt.trial_ids,
                        np.ascontiguousarray(combined, dtype=np.float64),
                        yelt.num_trials,
                        layers_json,
                    )
                else:
                    # Multi-input or weighted COMBINATION / PRICING_OUTPUT / YELT_OUTPUT / EP_OUTPUT
                    ev_out = combined
                    ann = np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials)
                    if compute_metrics:
                        mx = np.zeros(yelt.num_trials, dtype=np.float64)
                        np.maximum.at(mx, yelt.trial_ids - 1, ev_out)
                    else:
                        mx = _ZERO_TRIALS
                    rip_acc = np.zeros(yelt.num_trials, dtype=np.float64)
                    for e in in_edges:
                        if e.source in node_rip:
                            rip_acc += (e.sign * e.weight) * node_rip[e.source]
                    rip = rip_acc

                if compute_metrics:
                    ep = _ep_stats_from_vector(ann)
                    _cache_put(node_key, (ev_out, ann, mx, rip, ep))
                else:
                    ep = None

            if need_events or remaining_uses[nid] > 0:
                node_event_streams[nid] = ev_out
            node_ann[nid] = ann
            node_rip[nid] = rip
            if compute_metrics:
                node_mx[nid] = mx
                if ep is not None:
                    node_ep[nid] = ep

            # Free upstream event streams once their last consumer has evaluated (when caller doesn't need events)
            if not need_events:
                for e in in_edges:
                    remaining_uses[e.source] -= 1
                    if remaining_uses[e.source] <= 0:
                        node_event_streams.pop(e.source, None)

            if compute_metrics and ep is not None:
                max_occ_val = float(np.max(mx)) if len(mx) else 0.0
                exp_rip = float(np.mean(rip))
                placed_share = _opt_float(node.params.get("placedShare"), 1.0)
                prem = _opt_float(node.params.get("depositPremium"), 0.0) * placed_share
                raw_lim = next(
                    (node.params.get(k) for k in ("occLimit", "aggLimit", "limit") if not _is_blank_param(node.params.get(k))),
                    0.0,
                )
                lim = _opt_float(raw_lim, 0.0) * placed_share
                rol = (prem / lim) if lim > 0 and prem > 0 else None
                # Loss ratio needs this node's own premium. Pass-through outputs inherit RIP
                # from upstream terms; dividing EL by that RIP alone invents a ratio.
                elr = (ep["el"] / (prem + exp_rip)) if prem > 0 else None
                pricing = None
                link = (pricing_links or {}).get(nid) if node.node_type == NodeType.PRICING_OUTPUT else None
                if link is not None:
                    tvar_250 = next((pt["tvar"] for pt in ep["ep_points"] if abs(pt["return_period"] - 250.0) < 1e-6), 0.0)
                    pricing = compute_pricing_economics(link, in_edges, nodes_by_id, ann, rip, tvar_250)
                    rol = pricing.rol
                    elr = pricing.loss_ratio

                node_metrics.append(
                    NodeViewMetrics(
                        node_id=nid,
                        node_label=node.label,
                        node_type=node.node_type,
                        view=view,
                        currency=programme.currency,
                        el=round(ep["el"], 4),
                        sd=round(ep["sd"], 4),
                        max_occ=round(max_occ_val, 4),
                        max_year=round(ep["max_val"], 4),
                        expected_rip=round(exp_rip, 4),
                        rol=round(rol, 4) if rol is not None else None,
                        elr=round(elr, 4) if elr is not None else None,
                        ep_points=[EpPoint(**pt) for pt in ep["ep_points"]],
                        pricing=pricing,
                    )
                )

        views_output[view_key] = node_metrics
        event_vectors[view_key] = node_event_streams
        if not compute_metrics and not need_events:
            keep_ids = {
                n.node_id
                for n in programme.graph.nodes
                if n.node_type in LAYER_NODE_TYPES
                or n.node_type in (NodeType.PRICING_OUTPUT, NodeType.YELT_OUTPUT, NodeType.EP_OUTPUT)
            }
            node_ann = {k: v for k, v in node_ann.items() if k in keep_ids}
            node_rip = {k: v for k, v in node_rip.items() if k in keep_ids}
        annual_vectors[view_key] = node_ann
        rip_vectors[view_key] = node_rip

    compute_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    breadcrumb = [
        BreadcrumbStep(entity_type="SIM_SET", entity_id=yelt.sim_set_id, label=yelt.name, content_hash=yelt.content_hash),
    ]
    input_hashes: dict[str, str] = {
        "sim_set": yelt.content_hash,
        "uplift_set": programme.uplift_set.content_hash,
        "graph": programme.graph.content_hash,
    }
    if used_industry_yelt and industry_yelt is not None:
        breadcrumb.append(
            BreadcrumbStep(
                entity_type="INDUSTRY_SIM_SET",
                entity_id=industry_yelt.sim_set_id,
                label=industry_yelt.name,
                content_hash=industry_yelt.content_hash,
            )
        )
        input_hashes["industry_sim_set"] = industry_yelt.content_hash
    for snap_date, snap_hash in sorted(used_snapshots.items()):
        breadcrumb.append(
            BreadcrumbStep(
                entity_type="PORTFOLIO_SNAPSHOT",
                entity_id=f"unl-snapshot-{snap_date}",
                label=f"UNL Portfolio Snapshot ({snap_date})",
                content_hash=snap_hash,
            )
        )
        input_hashes[f"portfolio_snapshot:{snap_date}"] = snap_hash
    breadcrumb.extend([
        BreadcrumbStep(
            entity_type="UPLIFT_SET",
            entity_id=programme.uplift_set.uplift_set_id,
            label=programme.uplift_set.name,
            version=f"v{programme.uplift_set.version}",
            content_hash=programme.uplift_set.content_hash,
        ),
        BreadcrumbStep(
            entity_type="PROGRAMME",
            entity_id=programme.programme_id,
            label=f"{programme.cedent_name} — {programme.programme_name}",
        ),
        BreadcrumbStep(
            entity_type="DAG_GRAPH",
            entity_id=programme.graph.graph_id,
            label=programme.graph.name,
            version=f"v{programme.graph.version}",
            content_hash=programme.graph.content_hash,
        ),
    ])
    manifest = lineage.record_manifest(
        run_type="DAG_EVAL",
        input_hashes=input_hashes,
        parameters={"programme_id": programme.programme_id, "views": [v.value for v in target_views]},
        output_payload={k: [m.model_dump(by_alias=True) for m in v] for k, v in views_output.items()},
        breadcrumb=breadcrumb,
    )

    resp = DagEvalResponse(
        programme_id=programme.programme_id,
        graph_id=programme.graph.graph_id,
        graph_version=programme.graph.version,
        currency=programme.currency,
        compute_ms=compute_ms,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        views=views_output,
        manifest=manifest,
    )
    return resp, {"annual": annual_vectors, "rip": rip_vectors, "events": event_vectors}


def evaluate_portfolio_rollup(
    *,
    portfolio_id: str,
    portfolio_name: str,
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    view: ViewOfRisk,
    total_capital: float,
    lineage: LineageRegistry,
    all_programmes: dict[str, ProgrammeDeal] | None = None,
    industry_yelt: YeltTable | None = None,
    portfolio_unl_resolver: Callable[[str | None, ViewOfRisk], tuple[np.ndarray, str]] | None = None,
    include_hedges: bool = True,
) -> PortfolioRollupResponse:
    """Roll up bound programmes/layers into a fund portfolio with ROC/IRR CDF and Euler Co-TVaR.

    Long (assumed) layers contribute positive loss, premium, RIP, and collateral.
    Hedge (ceded/retro) layers contribute negative loss (recoveries), negative premium (cost),
    negative RIP, and negative Euler Co-TVaR (tail capital relief).
    """
    t0 = time.perf_counter()
    active_progs = [p for p in programmes if include_hedges or not is_hedge_direction(p.direction)]
    currencies = {p.currency for p in active_progs}
    if len(currencies) > 1:
        raise ValueError(f"Cannot silently mix currencies in portfolio rollup without FX_CONVERSION: {sorted(currencies)}")
    portfolio_currency = next(iter(currencies), yelt.currency)
    long_progs = [p for p in active_progs if not is_hedge_direction(p.direction)]
    hedge_progs = [p for p in active_progs if is_hedge_direction(p.direction)]

    layer_rows_meta: list[dict[str, Any]] = []
    layer_ann_list: list[np.ndarray] = []
    layer_rip_list: list[np.ndarray] = []
    breadcrumbs: list[BreadcrumbStep] = [
        BreadcrumbStep(entity_type="SIM_SET", entity_id=yelt.sim_set_id, label=yelt.name, content_hash=yelt.content_hash)
    ]
    input_hashes: dict[str, str] = {"sim_set": yelt.content_hash}
    if industry_yelt is not None and any(is_ilw_programme(p.structure, p.instrument) for p in active_progs):
        breadcrumbs.append(
            BreadcrumbStep(
                entity_type="INDUSTRY_SIM_SET",
                entity_id=industry_yelt.sim_set_id,
                label=industry_yelt.name,
                content_hash=industry_yelt.content_hash,
            )
        )
        input_hashes["industry_sim_set"] = industry_yelt.content_hash

    ann_by_prog: dict[str, dict[str, np.ndarray]] = {}
    rip_by_prog: dict[str, dict[str, np.ndarray]] = {}
    internal_lineage = LineageRegistry(persist=False)

    # Identify cross-linked programmes so self-linked programmes can be streamed one-by-one without
    # retaining every programme's full annual+RIP dicts simultaneously in RAM.
    cross_ids: set[str] = set()
    for p in active_progs:
        for n in p.graph.nodes:
            if n.node_type == NodeType.PRICING_OUTPUT:
                cid = str(n.params.get("contractId") or "").strip()
                if cid and cid != p.programme_id:
                    cross_ids.add(p.programme_id)
                    cross_ids.add(cid)

    streamed_entries: list[Any] = []
    deferred_progs: list[ProgrammeDeal] = []
    port_rip = np.zeros(yelt.num_trials, dtype=np.float64)
    _empty_f64 = np.empty(0, dtype=np.float64)

    def _collect_prog(prog: ProgrammeDeal, ann_map: dict[str, np.ndarray], rip_map: dict[str, np.ndarray]) -> None:
        if prog.programme_id in cross_ids:
            deferred_progs.append(prog)
            ann_by_prog[prog.programme_id] = ann_map
            rip_by_prog[prog.programme_id] = rip_map
        else:
            local = extract_rollup_layers(
                [prog],
                {prog.programme_id: ann_map},
                {prog.programme_id: rip_map},
                all_programmes=all_programmes,
                include_1x=False,
                consume_maps=True,
            )
            for e in local:
                if len(e.rip_vec):
                    if e.is_hedge:
                        np.subtract(port_rip, e.rip_vec, out=port_rip)
                    else:
                        np.add(port_rip, e.rip_vec, out=port_rip)
                    e.rip_vec = _empty_f64
            streamed_entries.extend(local)

    # Evaluate long programmes first so any UNL retro hedge can snapshot our own UNL book
    for prog in long_progs:
        _, vecs = evaluate_programme_dag(
            prog,
            yelt,
            internal_lineage,
            views=[view],
            industry_yelt=industry_yelt,
            compute_metrics=False,
            need_events=False,
        )
        _collect_prog(prog, vecs["annual"][view.value], vecs["rip"][view.value])
        del vecs
        breadcrumbs.append(
            BreadcrumbStep(
                entity_type="PROGRAMME",
                entity_id=prog.programme_id,
                label=f"{prog.cedent_name} — {prog.programme_name}",
                version=f"v{prog.graph.version}",
                content_hash=prog.graph.content_hash,
            )
        )
        input_hashes[prog.programme_id] = prog.graph.content_hash

    fallback_unl_cache: dict[str, tuple[np.ndarray, str]] = {}

    def _effective_unl_resolver(as_of: str | None, v: ViewOfRisk) -> tuple[np.ndarray, str]:
        if portfolio_unl_resolver is not None:
            return portfolio_unl_resolver(as_of, v)
        key = f"{as_of or ''}:{v.value}"
        if key not in fallback_unl_cache:
            unl_deals = [p for p in long_progs if not is_ilw_programme(p.structure, p.instrument)]
            fallback_unl_cache[key] = compute_unl_portfolio_events(
                unl_deals,
                yelt,
                v,
                all_deals=all_programmes,
                as_of_date=as_of,
            )
        return fallback_unl_cache[key]

    for prog in hedge_progs:
        dag_resp, vecs = evaluate_programme_dag(
            prog,
            yelt,
            internal_lineage,
            views=[view],
            industry_yelt=industry_yelt,
            portfolio_unl_resolver=_effective_unl_resolver,
            compute_metrics=False,
            need_events=False,
        )
        _collect_prog(prog, vecs["annual"][view.value], vecs["rip"][view.value])
        del vecs
        for k, h in dag_resp.manifest.input_hashes.items():
            if k.startswith("portfolio_snapshot:"):
                input_hashes[k] = h
        breadcrumbs.append(
            BreadcrumbStep(
                entity_type="PROGRAMME",
                entity_id=prog.programme_id,
                label=f"{prog.cedent_name} — {prog.programme_name} (Hedge)",
                version=f"v{prog.graph.version}",
                content_hash=prog.graph.content_hash,
            )
        )
        input_hashes[prog.programme_id] = prog.graph.content_hash

    entries = streamed_entries
    if deferred_progs:
        entries.extend(
            extract_rollup_layers(
                deferred_progs,
                ann_by_prog,
                rip_by_prog,
                all_programmes=all_programmes,
                include_1x=False,
                consume_maps=True,
            )
        )
    ann_by_prog.clear()
    rip_by_prog.clear()

    m_layers = len(entries)
    layer_matrix = np.empty((m_layers, yelt.num_trials), dtype=np.float64)
    expected_hedge_recovery = 0.0

    for idx in range(m_layers):
        entry = entries[idx]
        entries[idx] = None  # type: ignore[call-overload]
        sign = -1.0 if entry.is_hedge else 1.0
        if entry.is_hedge:
            np.negative(entry.loss_vec, out=layer_matrix[idx])
            if len(entry.rip_vec):
                port_rip -= entry.rip_vec
            expected_hedge_recovery += float(np.mean(entry.loss_vec))
        else:
            layer_matrix[idx] = entry.loss_vec
            if len(entry.rip_vec):
                port_rip += entry.rip_vec
        ep_l = _ep_stats_from_vector(entry.loss_vec)
        sa_tvar_250_pos = next(p["tvar"] for p in ep_l["ep_points"] if abs(p["return_period"] - 250.0) < 1e-6)
        layer_rows_meta.append({
            "programme_id": entry.programme_id,
            "layer_id": entry.layer_id,
            "layer_name": entry.layer_name,
            "cedent_name": entry.cedent_name,
            "direction": entry.direction,
            "is_hedge": entry.is_hedge,
            "limit": entry.limit,
            "premium": entry.premium,
            "signed_premium": sign * entry.premium,
            "signed_line": entry.signed_line,
            "raw_el": ep_l["el"],
            "sa_tvar_250_pos": sa_tvar_250_pos,
            "signed_exp_rip": sign * entry.expected_rip,
        })
        del entry
    entries.clear()

    port_loss = layer_matrix.sum(axis=0)
    gross_premium = sum(r["premium"] for r in layer_rows_meta if not r["is_hedge"])
    hedge_premium = sum(r["premium"] for r in layer_rows_meta if r["is_hedge"])
    net_premium = gross_premium - hedge_premium
    deployed_collateral = sum(
        deployed_capital_m(r["limit"], r["premium"], is_hedge=r["is_hedge"]) for r in layer_rows_meta
    )
    capital_utilization_pct = (deployed_collateral / total_capital) * 100.0 if total_capital > 0 else 0.0
    capital_headroom_m = total_capital - deployed_collateral
    capital_exceeded = deployed_collateral > total_capital + 1e-6
    hedge_count = sum(1 for r in layer_rows_meta if r["is_hedge"])

    net_yield_on_capital = total_capital * 0.03
    trial_profit = net_premium + port_rip + net_yield_on_capital - port_loss
    trial_roc_pct = (trial_profit / total_capital) * 100.0

    port_var_250, port_tvar_250, co_tvars = rle.euler_cotvar(
        np.ascontiguousarray(layer_matrix, dtype=np.float64), 250.0
    )
    del layer_matrix
    _trim_memory()
    cotvar_sum_diff = abs(float(np.sum(co_tvars)) - float(port_tvar_250))

    alloc_rows: list[LayerAllocationRow] = []
    for idx, meta in enumerate(layer_rows_meta):
        is_hedge = meta["is_hedge"]
        sa_tvar_250_pos = meta["sa_tvar_250_pos"]
        co_tvar = float(co_tvars[idx])
        el_signed = -meta["raw_el"] if is_hedge else meta["raw_el"]
        sa_tvar_signed = -sa_tvar_250_pos if is_hedge else sa_tvar_250_pos
        if is_hedge:
            div_credit = (1.0 - abs(co_tvar) / sa_tvar_250_pos) * 100.0 if sa_tvar_250_pos > 0 else 0.0
        else:
            div_credit = (1.0 - co_tvar / sa_tvar_250_pos) * 100.0 if sa_tvar_250_pos > 0 else 0.0
        exp_rip_l = meta["signed_exp_rip"]
        exp_profit_l = meta["signed_premium"] + exp_rip_l - el_signed
        if is_hedge:
            # Cost per dollar of tail TVaR saved (positive = cost % of capital released)
            m_roc = (exp_profit_l / co_tvar) * 100.0 if co_tvar < -1e-9 else 0.0
        else:
            m_roc = (exp_profit_l / co_tvar) * 100.0 if co_tvar > 0 else 0.0
        alloc_rows.append(
            LayerAllocationRow(
                programme_id=meta["programme_id"],
                layer_id=meta["layer_id"],
                layer_name=meta["layer_name"],
                cedent_name=meta["cedent_name"],
                direction=meta["direction"],
                is_hedge=is_hedge,
                limit=round(meta["limit"], 4),
                premium=round(meta["signed_premium"], 4),
                signed_line=round(meta["signed_line"], 4),
                el=round(el_signed, 4),
                expected_rip=round(exp_rip_l, 4),
                standalone_tvar_250=round(sa_tvar_signed, 4),
                euler_cotvar_250=round(co_tvar, 4),
                diversification_credit_pct=round(div_credit, 2),
                marginal_roc_pct=round(m_roc, 2),
            )
        )

    sorted_roc_desc = np.sort(trial_roc_pct)[::-1]
    n = len(sorted_roc_desc)
    sample_probs = list(np.linspace(0.0, 0.90, 46)) + list(np.linspace(0.91, 0.99, 17)) + [0.992, 0.994, 0.996, 0.998, 0.999, 0.9995, 1.0]
    cdf_curve = []
    for p in sample_probs:
        idx_p = min(n - 1, max(0, int(round(p * (n - 1)))))
        cdf_curve.append(CdfPoint(cumulative_prob=round(p * 100.0, 2), roc_pct=round(float(sorted_roc_desc[idx_p]), 2)))

    def pct_val(p: float) -> float:
        idx_p = min(n - 1, max(0, int(round(p * (n - 1)))))
        return round(float(sorted_roc_desc[idx_p]), 2)

    quantiles = {
        "mean": round(float(np.mean(trial_roc_pct)), 2),
        "max": round(float(sorted_roc_desc[0]), 2),
        "noEvents": round(((net_premium + net_yield_on_capital) / total_capital) * 100.0, 2),
        "25.0th": pct_val(0.25),
        "50.0th": pct_val(0.50),
        "75.0th": pct_val(0.75),
        "90.0th": pct_val(0.90),
        "95.0th": pct_val(0.95),
        "98.0th": pct_val(0.98),
        "99.0th": pct_val(0.99),
        "99.6th": pct_val(0.996),
    }

    guidelines = [
        GuidelineCheck(percentile=95.0, label="95.0th% (1-in-20)", threshold_roc_pct=-15.0, actual_roc_pct=quantiles["95.0th"], passed=quantiles["95.0th"] >= -15.0),
        GuidelineCheck(percentile=98.0, label="98.0th% (1-in-50)", threshold_roc_pct=-30.0, actual_roc_pct=quantiles["98.0th"], passed=quantiles["98.0th"] >= -30.0),
        GuidelineCheck(percentile=99.0, label="99.0th% (1-in-100)", threshold_roc_pct=-45.0, actual_roc_pct=quantiles["99.0th"], passed=quantiles["99.0th"] >= -45.0),
        GuidelineCheck(percentile=99.6, label="99.6th% (1-in-250)", threshold_roc_pct=-65.0, actual_roc_pct=quantiles["99.6th"], passed=quantiles["99.6th"] >= -65.0),
    ]

    compute_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    exp_loss = float(np.mean(port_loss))
    exp_rip = float(np.mean(port_rip))
    exp_profit = float(np.mean(trial_profit))
    break_even_prob = float(np.mean(trial_profit >= 0.0)) * 100.0
    rotvar = (exp_profit / float(port_tvar_250)) * 100.0 if float(port_tvar_250) > 1e-9 else 0.0

    manifest = lineage.record_manifest(
        run_type="PORTFOLIO_ROLLUP",
        input_hashes=input_hashes,
        parameters={
            "portfolio_id": portfolio_id,
            "view": view.value,
            "total_capital": total_capital,
            "include_hedges": include_hedges,
        },
        output_payload={
            "quantiles": quantiles,
            "portfolio_tvar_250": round(float(port_tvar_250), 4),
            "include_hedges": include_hedges,
        },
        breadcrumb=breadcrumbs,
    )

    return PortfolioRollupResponse(
        portfolio_id=portfolio_id,
        portfolio_name=portfolio_name,
        view=view,
        currency=portfolio_currency,
        num_trials=yelt.num_trials,
        compute_ms=compute_ms,
        include_hedges=include_hedges,
        hedge_count=hedge_count,
        total_capital=round(total_capital, 4),
        deployed_collateral=round(deployed_collateral, 4),
        capital_utilization_pct=round(capital_utilization_pct, 2),
        capital_headroom_m=round(capital_headroom_m, 4),
        capital_exceeded=capital_exceeded,
        gross_premium=round(gross_premium, 4),
        hedge_premium=round(hedge_premium, 4),
        net_premium=round(net_premium, 4),
        expected_rip=round(exp_rip, 4),
        expected_loss=round(exp_loss, 4),
        expected_hedge_recovery=round(expected_hedge_recovery, 4),
        expected_profit=round(exp_profit, 4),
        mean_roc_pct=quantiles["mean"],
        no_event_roc_pct=quantiles["noEvents"],
        break_even_prob_pct=round(break_even_prob, 2),
        return_on_tvar_pct=round(rotvar, 2),
        portfolio_var_250=round(float(port_var_250), 4),
        portfolio_tvar_250=round(float(port_tvar_250), 4),
        cotvar_sum_check_diff=cotvar_sum_diff,
        cdf_curve=cdf_curve,
        quantiles=quantiles,
        guidelines=guidelines,
        layer_allocations=alloc_rows,
        manifest=manifest,
    )


def evaluate_portfolio_hedge_comparison(
    *,
    portfolio_id: str,
    portfolio_name: str,
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    view: ViewOfRisk,
    total_capital: float,
    lineage: LineageRegistry,
    all_programmes: dict[str, ProgrammeDeal] | None = None,
    industry_yelt: YeltTable | None = None,
    portfolio_unl_resolver: Callable[[str | None, ViewOfRisk], tuple[np.ndarray, str]] | None = None,
) -> PortfolioHedgeComparisonResponse:
    """Compare portfolio metrics with hedges (net) vs without hedges (long-only) and evaluate accretion."""
    without_hedges = evaluate_portfolio_rollup(
        portfolio_id=f"{portfolio_id}-gross",
        portfolio_name=f"{portfolio_name} (Without Hedges)",
        programmes=programmes,
        yelt=yelt,
        view=view,
        total_capital=total_capital,
        lineage=lineage,
        all_programmes=all_programmes,
        industry_yelt=industry_yelt,
        portfolio_unl_resolver=portfolio_unl_resolver,
        include_hedges=False,
    )
    with_hedges = evaluate_portfolio_rollup(
        portfolio_id=portfolio_id,
        portfolio_name=portfolio_name,
        programmes=programmes,
        yelt=yelt,
        view=view,
        total_capital=total_capital,
        lineage=lineage,
        all_programmes=all_programmes,
        industry_yelt=industry_yelt,
        portfolio_unl_resolver=portfolio_unl_resolver,
        include_hedges=True,
    )

    has_hedges = with_hedges.hedge_count > 0
    net_hedge_cost = with_hedges.hedge_premium - with_hedges.expected_hedge_recovery
    delta_profit = with_hedges.expected_profit - without_hedges.expected_profit
    delta_mean_roc = with_hedges.mean_roc_pct - without_hedges.mean_roc_pct
    delta_no_event = with_hedges.no_event_roc_pct - without_hedges.no_event_roc_pct
    delta_be = with_hedges.break_even_prob_pct - without_hedges.break_even_prob_pct
    delta_var = with_hedges.portfolio_var_250 - without_hedges.portfolio_var_250
    delta_tvar = with_hedges.portfolio_tvar_250 - without_hedges.portfolio_tvar_250
    tvar_saved = without_hedges.portfolio_tvar_250 - with_hedges.portfolio_tvar_250
    gross_rotvar = without_hedges.return_on_tvar_pct
    net_rotvar = with_hedges.return_on_tvar_pct
    delta_rotvar = net_rotvar - gross_rotvar
    cost_per_tvar = (net_hedge_cost / tvar_saved) if tvar_saved > 1e-6 else None
    breaches_without = sum(1 for g in without_hedges.guidelines if not g.passed)
    breaches_with = sum(1 for g in with_hedges.guidelines if not g.passed)

    if not has_hedges:
        is_accretive = False
        verdict = "No Active Hedges"
        reason = "No bound hedge programmes in the portfolio yet. Bind an ILW or UNL retro hedge in Contracts to evaluate accretion."
    elif delta_rotvar > 0 or (breaches_with < breaches_without and tvar_saved > 0):
        is_accretive = True
        verdict = "Accretive"
        if delta_rotvar > 0:
            reason = (
                f"Hedges lift Return on 1-in-250 TVaR by {delta_rotvar:+.2f} pts "
                f"({gross_rotvar:.2f}% → {net_rotvar:.2f}%) and save ${tvar_saved:.2f}m of tail TVaR."
            )
        else:
            reason = (
                f"Hedges resolve {breaches_without - breaches_with} risk-appetite breach(es) "
                f"and save ${tvar_saved:.2f}m of 1-in-250 TVaR."
            )
    else:
        is_accretive = False
        verdict = "Dilutive"
        reason = (
            f"Hedges reduce Return on 1-in-250 TVaR by {delta_rotvar:.2f} pts "
            f"({gross_rotvar:.2f}% → {net_rotvar:.2f}%); net hedge cost exceeds tail capital relief hurdle."
        )

    accretion = HedgeAccretionSummary(
        has_hedges=has_hedges,
        hedge_layer_count=with_hedges.hedge_count,
        hedge_premium_m=round(with_hedges.hedge_premium, 4),
        expected_hedge_recovery_m=round(with_hedges.expected_hedge_recovery, 4),
        net_hedge_cost_m=round(net_hedge_cost, 4),
        delta_expected_profit_m=round(delta_profit, 4),
        delta_mean_roc_pct=round(delta_mean_roc, 2),
        delta_no_event_roc_pct=round(delta_no_event, 2),
        delta_break_even_prob_pct=round(delta_be, 2),
        delta_var_250_m=round(delta_var, 4),
        delta_tvar_250_m=round(delta_tvar, 4),
        tvar_250_reduction_m=round(tvar_saved, 4),
        gross_return_on_tvar_pct=round(gross_rotvar, 2),
        net_return_on_tvar_pct=round(net_rotvar, 2),
        delta_return_on_tvar_pct=round(delta_rotvar, 2),
        cost_per_dollar_tvar_saved=round(cost_per_tvar, 4) if cost_per_tvar is not None else None,
        guidelines_breached_without=breaches_without,
        guidelines_breached_with=breaches_with,
        is_accretive=is_accretive,
        accretion_verdict=verdict,
        accretion_reason=reason,
    )
    comparison_manifest = lineage.record_manifest(
        run_type="PORTFOLIO_HEDGE_COMPARISON",
        input_hashes={
            "with_hedges": with_hedges.manifest.output_hash,
            "without_hedges": without_hedges.manifest.output_hash,
        },
        parameters={"portfolio_id": portfolio_id, "view": view.value, "total_capital": total_capital},
        output_payload=accretion.model_dump(by_alias=True),
        breadcrumb=[
            BreadcrumbStep(
                entity_type="PORTFOLIO_ROLLUP",
                entity_id=without_hedges.manifest.manifest_id,
                label=f"Without Hedges ({without_hedges.view.value})",
                content_hash=without_hedges.manifest.output_hash,
            ),
            BreadcrumbStep(
                entity_type="PORTFOLIO_ROLLUP",
                entity_id=with_hedges.manifest.manifest_id,
                label=f"With Hedges ({with_hedges.view.value})",
                content_hash=with_hedges.manifest.output_hash,
            ),
        ],
    )
    return PortfolioHedgeComparisonResponse(
        with_hedges=with_hedges,
        without_hedges=without_hedges,
        accretion=accretion,
        manifest=comparison_manifest,
    )


def evaluate_deterministic_run(
    req: DeterministicRunRequest,
    programme: ProgrammeDeal,
    lineage: LineageRegistry,
) -> DeterministicRunResponse:
    """Push any ordered loss list chronologically through a programme's layer structure via Rust."""
    validate_dag_acyclic(programme.graph)
    ordered_losses = sorted(req.losses, key=lambda r: r.occurrence_datetime)
    raw_amounts = np.array([r.ground_up_loss for r in ordered_losses], dtype=np.float64)
    rp_ids = np.array([RP_TO_ID.get(r.region_peril_key, -999) for r in ordered_losses], dtype=np.int32)

    if req.view == ViewOfRisk.ADJUSTED and programme.uplift_set.rules:
        adj_amounts = rle.apply_uplifts_yelt(raw_amounts, rp_ids, _serialize_uplift_rules(programme.uplift_set))
    else:
        adj_amounts = raw_amounts

    layer_statements: list[DeterministicLayerStatement] = []
    total_ceded_all = 0.0
    total_rip_all = 0.0

    nodes_by_id = {n.node_id: n for n in programme.graph.nodes}
    incoming_map: dict[str, list[str]] = {n.node_id: [] for n in programme.graph.nodes}
    for e in programme.graph.edges:
        incoming_map[e.target].append(e.source)

    for node in programme.graph.nodes:
        if node.node_type not in (NodeType.OCC_XL, NodeType.AGG_XL):
            continue

        subject = adj_amounts.copy()
        for src_id in incoming_map[node.node_id]:
            src = nodes_by_id[src_id]
            if src.node_type == NodeType.REGION_PERIL_FILTER:
                allowed = _allowed_rp_keys_for_filter(src.params)
                if allowed is not None:
                    for i, row in enumerate(ordered_losses):
                        if row.region_peril_key not in allowed:
                            subject[i] = 0.0

        if node.node_type == NodeType.OCC_XL:
            p_json = json.dumps({
                "occ_limit": float(node.params["occLimit"]),
                "occ_attachment": float(node.params["occAttachment"]),
                "occ_franchise": _opt_float(node.params.get("occFranchise"), 0.0),
                "reinstatements": _opt_nullable_float(node, "reinstatements"),
                "rip_rate": _opt_float(node.params.get("ripRate"), 0.0),
                "deposit_premium": _opt_float(node.params.get("depositPremium"), 0.0),
                "placed_share": _opt_float(node.params.get("placedShare"), 1.0),
            })
            raw_steps = json.loads(rle.trace_occ_xl(subject.tolist(), p_json))
        else:
            p_json = json.dumps({
                "agg_limit": float(node.params["aggLimit"]),
                "agg_attachment": float(node.params["aggAttachment"]),
                "per_event_franchise": _opt_float(node.params.get("perEventFranchise"), 0.0),
                "per_event_cap": _opt_nullable_float(node, "perEventCap"),
                "placed_share": _opt_float(node.params.get("placedShare"), 1.0),
            })
            raw_steps = json.loads(rle.trace_agg_xl(subject.tolist(), p_json))

        steps_out: list[DeterministicStepResult] = []
        layer_ceded = 0.0
        layer_rip = 0.0
        for i, st in enumerate(raw_steps):
            row = ordered_losses[i]
            layer_ceded += st["ceded_loss"]
            layer_rip += st["rip_due"]
            steps_out.append(
                DeterministicStepResult(
                    loss_id=row.loss_id,
                    event_name=row.event_name,
                    occurrence_datetime=row.occurrence_datetime,
                    region_peril_key=row.region_peril_key,
                    ground_up_loss=round(row.ground_up_loss, 4),
                    subject_loss=round(st["subject_loss"], 4),
                    loss_to_layer=round(st["loss_to_layer"], 4),
                    ceded_loss=round(st["ceded_loss"], 4),
                    rip_due=round(st["rip_due"], 4),
                    net_retained=round(st["subject_loss"] - st["ceded_loss"], 4),
                    state_before=LayerStateSnapshot(**st["state_before"]),
                    state_after=LayerStateSnapshot(**st["state_after"]),
                )
            )

        total_ceded_all += layer_ceded
        total_rip_all += layer_rip
        layer_statements.append(
            DeterministicLayerStatement(
                layer_id=node.node_id,
                layer_name=node.label,
                layer_type=node.node_type,
                total_subject_loss=round(float(subject.sum()), 4),
                total_ceded_loss=round(layer_ceded, 4),
                total_rip_due=round(layer_rip, 4),
                steps=steps_out,
            )
        )

    total_gu = float(raw_amounts.sum())
    loss_list_hash = canonical_hash([r.model_dump() for r in ordered_losses])
    breadcrumb = [
        BreadcrumbStep(entity_type="LOSS_LIST", entity_id=f"ll-{loss_list_hash[:8]}", label=f"Deterministic Loss List ({len(ordered_losses)} events)", content_hash=loss_list_hash),
        BreadcrumbStep(entity_type="PROGRAMME", entity_id=programme.programme_id, label=f"{programme.cedent_name} — {programme.programme_name}"),
        BreadcrumbStep(entity_type="DAG_GRAPH", entity_id=programme.graph.graph_id, label=programme.graph.name, version=f"v{programme.graph.version}", content_hash=programme.graph.content_hash),
    ]
    manifest = lineage.record_manifest(
        run_type="DETERMINISTIC_RUN",
        input_hashes={"loss_list": loss_list_hash, "graph": programme.graph.content_hash, "uplift_set": programme.uplift_set.content_hash},
        parameters={"programme_id": programme.programme_id, "view": req.view.value, "hours_clause": req.hours_clause},
        output_payload={"total_ceded": round(total_ceded_all, 4), "total_rip_due": round(total_rip_all, 4)},
        breadcrumb=breadcrumb,
    )

    return DeterministicRunResponse(
        programme_id=programme.programme_id,
        view=req.view,
        currency=programme.currency,
        hours_clause=req.hours_clause,
        total_ground_up=round(total_gu, 4),
        total_ceded=round(total_ceded_all, 4),
        total_rip_due=round(total_rip_all, 4),
        total_net_retained=round(total_gu - total_ceded_all, 4),
        layer_statements=layer_statements,
        manifest=manifest,
    )
