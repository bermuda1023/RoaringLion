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
import time
from collections import OrderedDict
from typing import Any

import numpy as np
import roaring_lion_engine as rle

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
    LayerAllocationRow,
    LayerStateSnapshot,
    NodeViewMetrics,
    PortfolioRollupResponse,
    ProgrammeDeal,
    UpliftSet,
)
from app.models.enums import NodeType, ViewOfRisk
from app.services.lineage import LineageRegistry, canonical_hash

DEFAULT_RETURN_PERIODS = [10.0, 20.0, 50.0, 100.0, 200.0, 250.0, 500.0, 1000.0]

# Content-addressed LRU cache for DAG intermediate node outputs:
# key -> (ev_out, ann, mx, rip, ep_dict)
_NODE_CACHE: OrderedDict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]] = OrderedDict()
_MAX_CACHE_ENTRIES = 128


def clear_dag_cache() -> None:
    _NODE_CACHE.clear()


def _cache_get(key: str):
    if key in _NODE_CACHE:
        _NODE_CACHE.move_to_end(key)
        return _NODE_CACHE[key]
    return None


def _cache_put(key: str, val) -> None:
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


def validate_dag_acyclic(graph: GraphSpec) -> list[str]:
    """Validate DAG acyclicity and return topological node order."""
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
) -> tuple[DagEvalResponse, dict[str, dict[str, np.ndarray]]]:
    """Evaluate a programme's DAG on UNADJUSTED and ADJUSTED views via Rust with content-addressed node caching."""
    t0 = time.perf_counter()
    target_views = views or [ViewOfRisk.UNADJUSTED, ViewOfRisk.ADJUSTED]
    nodes_by_id = {n.node_id: n for n in programme.graph.nodes}
    incoming: dict[str, list] = {n.node_id: [] for n in programme.graph.nodes}
    for edge in programme.graph.edges:
        incoming[edge.target].append(edge)

    topo = validate_dag_acyclic(programme.graph)

    views_output: dict[str, list[NodeViewMetrics]] = {}
    annual_vectors: dict[str, dict[str, np.ndarray]] = {}
    rip_vectors: dict[str, dict[str, np.ndarray]] = {}
    cache_hits = 0
    cache_misses = 0

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
        node_rip: dict[str, np.ndarray] = {}
        node_hashes: dict[str, str] = {}
        node_metrics: list[NodeViewMetrics] = []

        for nid in topo:
            node = nodes_by_id[nid]
            in_edges = incoming[nid]
            upstream_sig = [
                (node_hashes[e.source], e.sign, e.weight)
                for e in sorted(in_edges, key=lambda x: (x.source, x.edge_id))
            ]
            node_key = canonical_hash({
                "src": view_source_hash if not in_edges else "",
                "ind": industry_yelt.content_hash if (industry_yelt and node.node_type == NodeType.INDUSTRY_INDEX_SOURCE) else "",
                "upstream": upstream_sig,
                "type": node.node_type.value,
                "params": node.params,
            })
            node_hashes[nid] = node_key

            cached = _cache_get(node_key)
            if cached is not None:
                ev_out, ann, mx, rip, ep = cached
                cache_hits += 1
            else:
                cache_misses += 1
                if in_edges:
                    combined = np.zeros_like(base_losses)
                    for e in in_edges:
                        combined += (e.sign * e.weight) * node_event_streams[e.source]
                    combined = np.maximum(combined, 0.0)
                else:
                    combined = base_losses

                rip = np.zeros(yelt.num_trials, dtype=np.float64)

                if node.node_type == NodeType.LOSS_SOURCE:
                    ev_out = base_losses
                    ann = np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials)
                    mx = np.zeros(yelt.num_trials, dtype=np.float64)
                    np.maximum.at(mx, yelt.trial_ids - 1, ev_out)
                elif node.node_type == NodeType.INDUSTRY_INDEX_SOURCE:
                    ev_out = industry_yelt.losses if industry_yelt is not None else base_losses
                    ann = np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials)
                    mx = np.zeros(yelt.num_trials, dtype=np.float64)
                    np.maximum.at(mx, yelt.trial_ids - 1, ev_out)
                elif node.node_type == NodeType.REGION_PERIL_FILTER:
                    allowed = set(node.params.get("regionPerils", []))
                    if not allowed or "ALL" in allowed:
                        ev_out = combined
                    else:
                        allowed_ids = [RP_TO_ID[k] for k in allowed if k in RP_TO_ID]
                        mask = np.isin(yelt.rp_ids, allowed_ids)
                        ev_out = np.where(mask, combined, 0.0)
                    ann = np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials)
                    mx = np.zeros(yelt.num_trials, dtype=np.float64)
                    np.maximum.at(mx, yelt.trial_ids - 1, ev_out)
                elif node.node_type == NodeType.DATE_WINDOW_FILTER:
                    start_day = int(node.params.get("startDay", 1))
                    end_day = int(node.params.get("endDay", 365))
                    if start_day <= end_day:
                        mask = (yelt.day_of_year >= start_day) & (yelt.day_of_year <= end_day)
                    else:
                        mask = (yelt.day_of_year >= start_day) | (yelt.day_of_year <= end_day)
                    ev_out = np.where(mask, combined, 0.0)
                    ann = np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials)
                    mx = np.zeros(yelt.num_trials, dtype=np.float64)
                    np.maximum.at(mx, yelt.trial_ids - 1, ev_out)
                elif node.node_type == NodeType.LOSS_SIZE_FILTER:
                    min_l = float(node.params.get("minLoss", 0.0))
                    max_l = node.params.get("maxLoss", None)
                    mask = combined >= min_l
                    if max_l is not None:
                        mask &= combined <= float(max_l)
                    ev_out = np.where(mask, combined, 0.0)
                    ann = np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials)
                    mx = np.zeros(yelt.num_trials, dtype=np.float64)
                    np.maximum.at(mx, yelt.trial_ids - 1, ev_out)
                elif node.node_type == NodeType.EVENT_ORDER_FILTER:
                    eo_json = json.dumps({
                        "min_order": int(node.params.get("minOrder", 2)),
                        "max_order": node.params.get("maxOrder", None),
                        "min_loss_threshold": float(node.params.get("minLossThreshold", 0.0)),
                    })
                    ev_out = rle.eval_event_order_filter_yelt(
                        yelt.trial_ids,
                        np.ascontiguousarray(combined, dtype=np.float64),
                        yelt.num_trials,
                        eo_json,
                    )
                    ann = np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials)
                    mx = np.zeros(yelt.num_trials, dtype=np.float64)
                    np.maximum.at(mx, yelt.trial_ids - 1, ev_out)
                elif node.node_type == NodeType.UPLIFT:
                    factor = float(node.params.get("factor", 1.0))
                    add_load = float(node.params.get("additiveLoad", 0.0))
                    ev_out = np.where(combined > 0.0, np.maximum(combined * factor + add_load, 0.0), 0.0)
                    ann = np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials)
                    mx = np.zeros(yelt.num_trials, dtype=np.float64)
                    np.maximum.at(mx, yelt.trial_ids - 1, ev_out)
                elif node.node_type == NodeType.OCC_XL:
                    p_json = json.dumps({
                        "occ_limit": float(node.params["occLimit"]),
                        "occ_attachment": float(node.params["occAttachment"]),
                        "occ_franchise": float(node.params.get("occFranchise", 0.0)),
                        "reinstatements": node.params.get("reinstatements", None),
                        "rip_rate": float(node.params.get("ripRate", 0.0)),
                        "deposit_premium": float(node.params.get("depositPremium", 0.0)),
                        "placed_share": float(node.params.get("placedShare", 1.0)),
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
                        "per_event_franchise": float(node.params.get("perEventFranchise", 0.0)),
                        "per_event_cap": node.params.get("perEventCap", None),
                        "placed_share": float(node.params.get("placedShare", 1.0)),
                    })
                    ev_out, ann, mx = rle.eval_agg_xl_yelt(
                        yelt.trial_ids,
                        np.ascontiguousarray(combined, dtype=np.float64),
                        yelt.num_trials,
                        p_json,
                    )
                elif node.node_type in (NodeType.QUOTA_SHARE, NodeType.AGG_QUOTA_SHARE):
                    p_json = json.dumps({
                        "cession_share": float(node.params.get("cessionShare", 0.25)),
                        "event_cap": node.params.get("eventCap", None),
                        "agg_cap": node.params.get("aggCap", None),
                        "placed_share": float(node.params.get("placedShare", 1.0)),
                    })
                    ev_out, ann, mx = rle.eval_quota_share_yelt(
                        yelt.trial_ids,
                        np.ascontiguousarray(combined, dtype=np.float64),
                        yelt.num_trials,
                        p_json,
                    )
                elif node.node_type == NodeType.SURPLUS_SHARE:
                    p_json = json.dumps({
                        "retention_line": float(node.params.get("retentionLine", 25.0)),
                        "num_lines": float(node.params.get("numLines", 4.0)),
                        "placed_share": float(node.params.get("placedShare", 1.0)),
                    })
                    ev_out, ann, mx = rle.eval_surplus_share_yelt(
                        yelt.trial_ids,
                        np.ascontiguousarray(combined, dtype=np.float64),
                        yelt.num_trials,
                        p_json,
                    )
                elif node.node_type == NodeType.ILW_OPTION:
                    trig = float(node.params.get("trigger", 40.0))
                    lim = float(node.params.get("limit", 25.0))
                    placed = float(node.params.get("placedShare", 1.0))
                    ev_out = np.where(combined >= trig, lim * placed, 0.0)
                    mx = np.zeros(yelt.num_trials, dtype=np.float64)
                    np.maximum.at(mx, yelt.trial_ids - 1, ev_out)
                    ann = np.minimum(
                        np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials),
                        lim * placed,
                    )
                elif node.node_type == NodeType.CASCADING_TOWER:
                    raw_layers = node.params.get("layers", [])
                    layers_json = json.dumps([
                        {
                            "occ_limit": float(l["occLimit"]),
                            "occ_attachment": float(l["occAttachment"]),
                            "occ_franchise": float(l.get("occFranchise", 0.0)),
                            "reinstatements": l.get("reinstatements", 0.0),
                            "rip_rate": float(l.get("ripRate", 0.0)),
                            "deposit_premium": float(l.get("depositPremium", 0.0)),
                            "placed_share": float(l.get("placedShare", 1.0)),
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
                    # COMBINATION / PRICING_OUTPUT / YELT_OUTPUT / EP_OUTPUT pass-through
                    ev_out = combined
                    ann = np.bincount(yelt.trial_ids - 1, weights=ev_out, minlength=yelt.num_trials)
                    mx = np.zeros(yelt.num_trials, dtype=np.float64)
                    np.maximum.at(mx, yelt.trial_ids - 1, ev_out)
                    for e in in_edges:
                        if e.source in node_rip:
                            rip += (e.sign * e.weight) * node_rip[e.source]

                ep = _ep_stats_from_vector(ann)
                _cache_put(node_key, (ev_out, ann, mx, rip, ep))

            node_event_streams[nid] = ev_out
            node_ann[nid] = ann
            node_rip[nid] = rip

            max_occ_val = float(np.max(mx)) if len(mx) else 0.0
            exp_rip = float(np.mean(rip))
            prem = float(node.params.get("depositPremium", 0.0)) * float(node.params.get("placedShare", 1.0))
            lim = float(node.params.get("occLimit", node.params.get("aggLimit", node.params.get("limit", 0.0)))) * float(node.params.get("placedShare", 1.0))
            rol = (prem / lim) if lim > 0 and prem > 0 else None
            # Loss ratio needs this node's own premium. Pass-through outputs inherit RIP
            # from upstream terms; dividing EL by that RIP alone invents a ratio.
            elr = (ep["el"] / (prem + exp_rip)) if prem > 0 else None

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
                )
            )

        views_output[view_key] = node_metrics
        annual_vectors[view_key] = node_ann
        rip_vectors[view_key] = node_rip

    compute_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    breadcrumb = [
        BreadcrumbStep(entity_type="SIM_SET", entity_id=yelt.sim_set_id, label=yelt.name, content_hash=yelt.content_hash),
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
    ]
    manifest = lineage.record_manifest(
        run_type="DAG_EVAL",
        input_hashes={
            "sim_set": yelt.content_hash,
            "uplift_set": programme.uplift_set.content_hash,
            "graph": programme.graph.content_hash,
        },
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
    return resp, {"annual": annual_vectors, "rip": rip_vectors}


def evaluate_portfolio_rollup(
    *,
    portfolio_id: str,
    portfolio_name: str,
    programmes: list[ProgrammeDeal],
    yelt: YeltTable,
    view: ViewOfRisk,
    total_capital: float,
    lineage: LineageRegistry,
) -> PortfolioRollupResponse:
    """Roll up bound programmes/layers into a fund portfolio with ROC/IRR CDF and Euler Co-TVaR."""
    t0 = time.perf_counter()
    layer_rows_meta: list[dict[str, Any]] = []
    layer_ann_list: list[np.ndarray] = []
    layer_rip_list: list[np.ndarray] = []
    breadcrumbs: list[BreadcrumbStep] = [
        BreadcrumbStep(entity_type="SIM_SET", entity_id=yelt.sim_set_id, label=yelt.name, content_hash=yelt.content_hash)
    ]
    input_hashes: dict[str, str] = {"sim_set": yelt.content_hash}

    term_types = (
        NodeType.OCC_XL,
        NodeType.AGG_XL,
        NodeType.QUOTA_SHARE,
        NodeType.AGG_QUOTA_SHARE,
        NodeType.SURPLUS_SHARE,
        NodeType.CASCADING_TOWER,
    )

    for prog in programmes:
        _, vecs = evaluate_programme_dag(prog, yelt, lineage, views=[view])
        ann_map = vecs["annual"][view.value]
        rip_map = vecs["rip"][view.value]
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

        for node in prog.graph.nodes:
            if node.node_type in term_types:
                placed = float(node.params.get("placedShare", prog.signed_line))
                lim = float(node.params.get("occLimit", node.params.get("aggLimit", node.params.get("aggCap", prog.limit)))) * placed
                prem = float(node.params.get("depositPremium", 0.0)) * placed
                layer_ann_list.append(ann_map[node.node_id])
                layer_rip_list.append(rip_map[node.node_id])
                layer_rows_meta.append({
                    "programme_id": prog.programme_id,
                    "layer_id": node.node_id,
                    "layer_name": node.label,
                    "cedent_name": prog.cedent_name,
                    "limit": lim,
                    "premium": prem,
                    "signed_line": placed,
                })

    layer_matrix = np.vstack(layer_ann_list) if layer_ann_list else np.zeros((0, yelt.num_trials))
    rip_matrix = np.vstack(layer_rip_list) if layer_rip_list else np.zeros((0, yelt.num_trials))

    port_loss = layer_matrix.sum(axis=0)
    port_rip = rip_matrix.sum(axis=0)
    gross_premium = sum(r["premium"] for r in layer_rows_meta)
    deployed_collateral = sum(r["limit"] for r in layer_rows_meta)

    net_yield_on_capital = total_capital * 0.03
    trial_profit = gross_premium + port_rip + net_yield_on_capital - port_loss
    trial_roc_pct = (trial_profit / total_capital) * 100.0

    port_var_250, port_tvar_250, co_tvars = rle.euler_cotvar(
        np.ascontiguousarray(layer_matrix, dtype=np.float64), 250.0
    )
    cotvar_sum_diff = abs(float(np.sum(co_tvars)) - float(port_tvar_250))

    alloc_rows: list[LayerAllocationRow] = []
    for idx, meta in enumerate(layer_rows_meta):
        ep_l = _ep_stats_from_vector(layer_matrix[idx])
        sa_tvar_250 = next(p["tvar"] for p in ep_l["ep_points"] if abs(p["return_period"] - 250.0) < 1e-6)
        co_tvar = float(co_tvars[idx])
        div_credit = (1.0 - co_tvar / sa_tvar_250) * 100.0 if sa_tvar_250 > 0 else 0.0
        exp_rip_l = float(np.mean(rip_matrix[idx]))
        exp_profit_l = meta["premium"] + exp_rip_l - ep_l["el"]
        m_roc = (exp_profit_l / co_tvar) * 100.0 if co_tvar > 0 else 0.0
        alloc_rows.append(
            LayerAllocationRow(
                programme_id=meta["programme_id"],
                layer_id=meta["layer_id"],
                layer_name=meta["layer_name"],
                cedent_name=meta["cedent_name"],
                limit=round(meta["limit"], 4),
                premium=round(meta["premium"], 4),
                signed_line=round(meta["signed_line"], 4),
                el=round(ep_l["el"], 4),
                expected_rip=round(exp_rip_l, 4),
                standalone_tvar_250=round(sa_tvar_250, 4),
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
        "noEvents": round(((gross_premium + net_yield_on_capital) / total_capital) * 100.0, 2),
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

    manifest = lineage.record_manifest(
        run_type="PORTFOLIO_ROLLUP",
        input_hashes=input_hashes,
        parameters={"portfolio_id": portfolio_id, "view": view.value, "total_capital": total_capital},
        output_payload={"quantiles": quantiles, "portfolio_tvar_250": round(float(port_tvar_250), 4)},
        breadcrumb=breadcrumbs,
    )

    return PortfolioRollupResponse(
        portfolio_id=portfolio_id,
        portfolio_name=portfolio_name,
        view=view,
        currency="USD",
        num_trials=yelt.num_trials,
        compute_ms=compute_ms,
        total_capital=round(total_capital, 4),
        deployed_collateral=round(deployed_collateral, 4),
        gross_premium=round(gross_premium, 4),
        expected_rip=round(exp_rip, 4),
        expected_loss=round(exp_loss, 4),
        expected_profit=round(exp_profit, 4),
        mean_roc_pct=quantiles["mean"],
        no_event_roc_pct=quantiles["noEvents"],
        break_even_prob_pct=round(break_even_prob, 2),
        portfolio_var_250=round(float(port_var_250), 4),
        portfolio_tvar_250=round(float(port_tvar_250), 4),
        cotvar_sum_check_diff=cotvar_sum_diff,
        cdf_curve=cdf_curve,
        quantiles=quantiles,
        guidelines=guidelines,
        layer_allocations=alloc_rows,
        manifest=manifest,
    )


def evaluate_deterministic_run(
    req: DeterministicRunRequest,
    programme: ProgrammeDeal,
    lineage: LineageRegistry,
) -> DeterministicRunResponse:
    """Push any ordered loss list chronologically through a programme's layer structure via Rust."""
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
                allowed = set(src.params.get("regionPerils", []))
                if allowed and "ALL" not in allowed:
                    for i, row in enumerate(ordered_losses):
                        if row.region_peril_key not in allowed:
                            subject[i] = 0.0

        if node.node_type == NodeType.OCC_XL:
            p_json = json.dumps({
                "occ_limit": float(node.params["occLimit"]),
                "occ_attachment": float(node.params["occAttachment"]),
                "occ_franchise": float(node.params.get("occFranchise", 0.0)),
                "reinstatements": node.params.get("reinstatements", None),
                "rip_rate": float(node.params.get("ripRate", 0.0)),
                "deposit_premium": float(node.params.get("depositPremium", 0.0)),
                "placed_share": float(node.params.get("placedShare", 1.0)),
            })
            raw_steps = json.loads(rle.trace_occ_xl(subject.tolist(), p_json))
        else:
            p_json = json.dumps({
                "agg_limit": float(node.params["aggLimit"]),
                "agg_attachment": float(node.params["aggAttachment"]),
                "per_event_franchise": float(node.params.get("perEventFranchise", 0.0)),
                "per_event_cap": node.params.get("perEventCap", None),
                "placed_share": float(node.params.get("placedShare", 1.0)),
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
