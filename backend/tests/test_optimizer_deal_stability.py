"""Hand-computed and end-to-end tests for AnalyzeRe-style Frontier Deal Stability."""

from __future__ import annotations

import math

from fastapi.testclient import TestClient
import numpy as np

from app.engine.optimizer import (
    FrontierExplorerPoint,
    OptimizedLayerRow,
    PortfolioMetricsSnapshot,
    STABILITY_BOUND_TOL,
    STABILITY_CONSENSUS_FRACTION,
    STABILITY_DELTA_TOL,
    STABILITY_DISPERSION_IQR_CUTOFF,
    STABILITY_DISPERSION_STD_CUTOFF,
    compute_deal_stability,
)
from app.main import app

client = TestClient(app)


def _dummy_metrics() -> PortfolioMetricsSnapshot:
    return PortfolioMetricsSnapshot(
        expected_profit_m=10.0,
        mean_roc_pct=10.0,
        deployed_collateral_m=100.0,
        gross_premium_m=20.0,
        expected_loss_m=10.0,
        el_capital_ratio=0.1,
        risk_reward_ratio=0.1,
        tvar_50_m=50.0,
        tvar_100_m=70.0,
        tvar_250_m=90.0,
        tvar_500_m=110.0,
        tvar_1000_m=130.0,
        var_100_m=55.0,
        var_250_m=75.0,
    )


def _layer_row(
    *,
    key: str,
    cedent: str,
    region_perils: list[str],
    cur_share: float,
    opt_share: float,
    capital_1x: float,
    profit_1x: float,
) -> OptimizedLayerRow:
    return OptimizedLayerRow(
        layer_key=key,
        programme_id=f"prog-{key}",
        layer_id=f"layer-{key}",
        cedent_name=cedent,
        programme_name=f"{cedent} Cat XL",
        layer_name=f"Layer {key}",
        direction="ASSUMED",
        status="BOUND",
        region_perils=region_perils,
        current_share=cur_share,
        optimal_share=opt_share,
        delta_share=opt_share - cur_share,
        limit_1x_m=capital_1x + 5.0,
        premium_1x_m=5.0,
        optimal_limit_m=(capital_1x + 5.0) * opt_share,
        optimal_premium_m=5.0 * opt_share,
        optimal_capital_m=capital_1x * opt_share,
        optimal_el_m=max(0.0, (5.0 - profit_1x) * opt_share),
        optimal_expected_profit_m=profit_1x * opt_share,
        co_tvar_100_m=0.0,
        co_tvar_250_m=0.0,
        co_tvar_250_pct=0.0,
        co_var_250_m=0.0,
        co_var_250_pct=0.0,
    )


def test_deal_stability_hand_computed_fixture():
    """Verify exact classification, stats, sorting, and theme conservation on a hand-computed 6-point fixture."""
    specs = [
        # (key, cedent, region_perils, cur, shares_across_6_pts, cap_1x, prof_1x, bounds)
        ("A-max", "Alpha Re", ["US-MW-CS"], 0.20, [1.0, 1.0, 1.0, 1.0, 1.0, 1.0], 20.0, 4.0, (0.0, 1.0, False)),
        ("B-min", "Beta Ins", ["US-FL-WS", "US-TX-WS"], 0.25, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0], 40.0, 2.0, (0.0, 1.0, False)),
        ("C-swing", "Gamma Mut", ["US-CA-EQ"], 0.20, [0.0, 1.0, 0.0, 1.0, 0.0, 1.0], 30.0, 3.0, (0.0, 1.0, False)),
        ("D-grow", "Alpha Re", ["EU-DE-WS"], 0.10, [0.35, 0.40, 0.40, 0.45, 0.45, 0.40], 10.0, 2.0, (0.0, 1.0, False)),
        ("E-lock", "Delta Co", ["JP-TY-WS"], 0.15, [0.15, 0.15, 0.15, 0.15, 0.15, 0.15], 20.0, 1.0, (0.15, 0.15, True)),
    ]

    initial_layers = [
        _layer_row(key=k, cedent=c, region_perils=rps, cur_share=cur, opt_share=cur, capital_1x=cap1x, profit_1x=p1x)
        for k, c, rps, cur, _, cap1x, p1x, _ in specs
    ]

    frontier_pts: list[FrontierExplorerPoint] = []
    for pt_idx in range(6):
        pt_layers = [
            _layer_row(
                key=k,
                cedent=c,
                region_perils=rps,
                cur_share=cur,
                opt_share=ws[pt_idx],
                capital_1x=cap1x,
                profit_1x=p1x,
            )
            for k, c, rps, cur, ws, cap1x, p1x, _ in specs
        ]
        frontier_pts.append(
            FrontierExplorerPoint(
                point_index=pt_idx,
                target_tvar_250_m=50.0 + 10.0 * pt_idx,
                status="Optimal",
                solver="HiGHS LP",
                metrics=_dummy_metrics(),
                layers=pt_layers,
                constraints=[],
                return_period_contributions=[],
            )
        )

    # Append an Infeasible point with extreme shares to verify it is excluded from stability calculations
    frontier_pts.append(
        FrontierExplorerPoint(
            point_index=6,
            target_tvar_250_m=120.0,
            status="Infeasible",
            solver="HiGHS LP",
            metrics=_dummy_metrics(),
            layers=[
                _layer_row(key=k, cedent=c, region_perils=rps, cur_share=cur, opt_share=0.99, capital_1x=cap1x, profit_1x=p1x)
                for k, c, rps, cur, _, cap1x, p1x, _ in specs
            ],
            constraints=[],
            return_period_contributions=[],
        )
    )

    bounds_by_key = {k: b for k, _, _, _, _, _, _, b in specs}
    summary = compute_deal_stability(
        initial_layers=initial_layers,
        frontier_pts=frontier_pts,
        resolved_bounds_by_key=bounds_by_key,
        recommended_index=3,
    )

    assert summary.feasible_point_count == 6
    assert summary.total_point_count == 7
    assert summary.consistent_grow_count == 2
    assert summary.consistent_shrink_count == 1
    assert summary.unstable_count == 2

    # Thresholds echoed verbatim
    assert summary.thresholds.bound_tolerance == STABILITY_BOUND_TOL
    assert summary.thresholds.delta_tolerance == STABILITY_DELTA_TOL
    assert summary.thresholds.consensus_fraction == STABILITY_CONSENSUS_FRACTION
    assert summary.thresholds.dispersion_iqr_cutoff == STABILITY_DISPERSION_IQR_CUTOFF
    assert summary.thresholds.dispersion_std_cutoff == STABILITY_DISPERSION_STD_CUTOFF

    by_key = {r.layer_key: r for r in summary.deals}

    # Deal A: max on every point -> CONSISTENT_GROW
    a = by_key["A-max"]
    assert a.classification == "CONSISTENT_GROW"
    assert a.min_share == 1.0 and a.median_share == 1.0 and a.max_share == 1.0
    assert a.std_share == 0.0 and a.iqr_share == 0.0
    assert a.pct_at_max == 1.0 and a.grow_support == 1.0 and a.shrink_support == 0.0
    assert a.stability_score == 1.0
    assert a.recommended_share == 1.0

    # Deal B: min on every point -> CONSISTENT_SHRINK
    b = by_key["B-min"]
    assert b.classification == "CONSISTENT_SHRINK"
    assert b.min_share == 0.0 and b.median_share == 0.0 and b.max_share == 0.0
    assert b.std_share == 0.0 and b.iqr_share == 0.0
    assert b.pct_at_min == 1.0 and b.shrink_support == 1.0 and b.grow_support == 0.0
    assert b.stability_score == 1.0

    # Deal C: swings [0, 1, 0, 1, 0, 1] -> UNSTABLE
    c = by_key["C-swing"]
    assert c.classification == "UNSTABLE"
    assert c.min_share == 0.0 and c.median_share == 0.5 and c.max_share == 1.0
    assert math.isclose(c.std_share, 0.5, abs_tol=1e-4)
    assert math.isclose(c.iqr_share, 1.0, abs_tol=1e-4)
    assert c.grow_support == 0.5 and c.shrink_support == 0.5
    assert c.recommended_share == 1.0  # index 3

    # Deal D: consistently grows vs current (0.10 -> 0.35..0.45) without hitting 100%
    d = by_key["D-grow"]
    assert d.classification == "CONSISTENT_GROW"
    assert d.min_share == 0.35 and d.median_share == 0.40 and d.max_share == 0.45
    assert d.pct_at_max == 0.0 and d.pct_grown == 1.0 and d.grow_support == 1.0
    assert d.iqr_share < STABILITY_DISPERSION_IQR_CUTOFF
    assert d.std_share < STABILITY_DISPERSION_STD_CUTOFF

    # Deal E: locked -> UNSTABLE with locked=True
    e = by_key["E-lock"]
    assert e.classification == "UNSTABLE"
    assert e.locked is True
    assert e.stability_score == 0.0

    # Bucket sort order: CONSISTENT_GROW -> CONSISTENT_SHRINK -> UNSTABLE
    ordered_keys = [r.layer_key for r in summary.deals]
    assert ordered_keys == ["A-max", "D-grow", "B-min", "C-swing", "E-lock"]

    # Hand-computed Theme Summary checks:
    # Mean optimal capital across all 5 deals:
    #   A: 20*1.0 = 20.0
    #   B: 40*0.0 = 0.0
    #   C: 30*0.5 = 15.0
    #   D: 10*(2.45/6) = 4.083333...
    #   E: 20*0.15 = 3.0
    #   Total frontier-mean deployed capital = 42.083333...
    # Grow bucket (A + D) deployed capital = 24.083333... -> 57.23% of total
    total_opt_cap = 20.0 + 0.0 + 15.0 + (10.0 * 2.45 / 6.0) + 3.0
    grow_cap = 20.0 + (10.0 * 2.45 / 6.0)
    assert summary.grow_themes.deal_count == 2
    assert math.isclose(summary.grow_themes.deployed_capital_m, grow_cap, abs_tol=1e-3)
    assert math.isclose(summary.grow_themes.share_of_deployed_capital_pct, (grow_cap / total_opt_cap) * 100.0, abs_tol=0.05)
    assert len(summary.grow_themes.top_cedents) == 1
    assert summary.grow_themes.top_cedents[0].key == "Alpha Re"
    assert summary.grow_themes.top_cedents[0].deal_count == 2

    # Shrink bucket (B only): current capital = 40*0.25 = 10.0
    # Total current capital = 4.0 (A) + 10.0 (B) + 6.0 (C) + 1.0 (D) + 3.0 (E) = 24.0
    assert summary.shrink_themes.deal_count == 1
    assert math.isclose(summary.shrink_themes.deployed_capital_m, 10.0, abs_tol=1e-4)
    assert math.isclose(summary.shrink_themes.share_of_deployed_capital_pct, (10.0 / 24.0) * 100.0, abs_tol=0.05)
    # B has 2 regions ['US-FL', 'US-TX'] -> 5.0 each, conserving 10.0 total; 1 peril ['WS'] -> 10.0
    reg_map = {x.key: x for x in summary.shrink_themes.top_regions}
    assert set(reg_map.keys()) == {"US-FL", "US-TX"}
    assert math.isclose(reg_map["US-FL"].deployed_capital_m, 5.0, abs_tol=1e-4)
    assert math.isclose(reg_map["US-TX"].deployed_capital_m, 5.0, abs_tol=1e-4)
    assert math.isclose(sum(x.deployed_capital_m for x in summary.shrink_themes.top_regions), 10.0, abs_tol=1e-4)
    assert len(summary.shrink_themes.top_perils) == 1
    assert summary.shrink_themes.top_perils[0].key == "WS"
    assert math.isclose(summary.shrink_themes.top_perils[0].deployed_capital_m, 10.0, abs_tol=1e-4)


def test_deal_stability_narrow_band_disjoint_tolerances():
    """Relative ±25% banding on a small 4% line (3%–5%) keeps min/max tolerances disjoint."""
    init = [
        _layer_row(key="grow-narrow", cedent="C1", region_perils=["US-FL-WS"], cur_share=0.04, opt_share=0.04, capital_1x=50.0, profit_1x=5.0),
        _layer_row(key="shrink-narrow", cedent="C2", region_perils=["US-FL-WS"], cur_share=0.04, opt_share=0.04, capital_1x=50.0, profit_1x=5.0),
    ]
    pts = [
        FrontierExplorerPoint(
            point_index=i,
            target_tvar_250_m=50.0 + i * 5.0,
            status="Optimal",
            solver="HiGHS LP",
            metrics=_dummy_metrics(),
            layers=[
                _layer_row(key="grow-narrow", cedent="C1", region_perils=["US-FL-WS"], cur_share=0.04, opt_share=0.05, capital_1x=50.0, profit_1x=5.0),
                _layer_row(key="shrink-narrow", cedent="C2", region_perils=["US-FL-WS"], cur_share=0.04, opt_share=0.03, capital_1x=50.0, profit_1x=5.0),
            ],
            constraints=[],
            return_period_contributions=[],
        )
        for i in range(6)
    ]
    bounds = {
        "grow-narrow": (0.03, 0.05, False),
        "shrink-narrow": (0.03, 0.05, False),
    }
    summary = compute_deal_stability(initial_layers=init, frontier_pts=pts, resolved_bounds_by_key=bounds)
    by_key = {r.layer_key: r for r in summary.deals}
    assert by_key["grow-narrow"].classification == "CONSISTENT_GROW"
    assert by_key["shrink-narrow"].classification == "CONSISTENT_SHRINK"


def test_deal_stability_negative_total_return_preserves_signed_share():
    """When total portfolio expected return is negative (e.g. hedge-heavy book), shareOfExpectedReturnPct uses the signed denominator."""
    # Hand-computed fixture:
    #   G-pos: cur=0.20, opt=[1.0]*6, cap_1x=20, prof_1x=+2.0 -> cur_ret=+0.4, mean_opt_ret=+2.0 (CONSISTENT_GROW)
    #   H-neg: cur=0.50, opt=[0.5]*6, cap_1x=10, prof_1x=-20.0, locked -> cur_ret=-10.0, mean_opt_ret=-10.0 (UNSTABLE / locked hedge)
    #   S-neg: cur=0.40, opt=[0.0]*6, cap_1x=25, prof_1x=-5.0 -> cur_ret=-2.0, mean_opt_ret=0.0 (CONSISTENT_SHRINK)
    # Totals:
    #   total_mean_opt_ret = +2.0 + (-10.0) + 0.0 = -8.0
    #     grow_themes: expected_return_m = +2.0 -> share = (+2.0 / -8.0) * 100 = -25.0%
    #   total_cur_ret = +0.4 + (-10.0) + (-2.0) = -11.6
    #     shrink_themes: expected_return_m = -2.0 -> share = (-2.0 / -11.6) * 100 = +17.24%
    specs = [
        ("G-pos", "Alpha Re", ["US-MW-CS"], 0.20, [1.0] * 6, 20.0, 2.0, (0.0, 1.0, False)),
        ("H-neg", "Retro Hedge", ["US-FL-WS"], 0.50, [0.5] * 6, 10.0, -20.0, (0.5, 0.5, True)),
        ("S-neg", "Beta Ins", ["US-CA-EQ"], 0.40, [0.0] * 6, 25.0, -5.0, (0.0, 1.0, False)),
    ]
    init = [
        _layer_row(key=k, cedent=c, region_perils=rps, cur_share=cur, opt_share=cur, capital_1x=cap1x, profit_1x=p1x)
        for k, c, rps, cur, _, cap1x, p1x, _ in specs
    ]
    pts = [
        FrontierExplorerPoint(
            point_index=i,
            target_tvar_250_m=50.0 + i * 5.0,
            status="Optimal",
            solver="HiGHS LP",
            metrics=_dummy_metrics(),
            layers=[
                _layer_row(
                    key=k,
                    cedent=c,
                    region_perils=rps,
                    cur_share=cur,
                    opt_share=ws[i],
                    capital_1x=cap1x,
                    profit_1x=p1x,
                )
                for k, c, rps, cur, ws, cap1x, p1x, _ in specs
            ],
            constraints=[],
            return_period_contributions=[],
        )
        for i in range(6)
    ]
    bounds = {k: b for k, _, _, _, _, _, _, b in specs}
    summary = compute_deal_stability(initial_layers=init, frontier_pts=pts, resolved_bounds_by_key=bounds)

    # Frontier-mean total return = -8.0; Always Grow bucket return = +2.0 -> -25.0% (NOT +25.0%)
    assert math.isclose(summary.grow_themes.expected_return_m, 2.0, abs_tol=1e-4)
    assert math.isclose(summary.grow_themes.share_of_expected_return_pct, -25.0, abs_tol=1e-2)
    assert math.isclose(summary.grow_themes.top_cedents[0].share_of_expected_return_pct, -25.0, abs_tol=1e-2)
    assert math.isclose(summary.grow_themes.top_regions[0].share_of_expected_return_pct, -25.0, abs_tol=1e-2)
    assert math.isclose(summary.grow_themes.top_perils[0].share_of_expected_return_pct, -25.0, abs_tol=1e-2)

    # Current-book total return = -11.6; Always Shrink bucket return = -2.0 -> +17.24% (NOT -17.24%)
    expected_shrink_pct = round((-2.0 / -11.6) * 100.0, 2)
    assert math.isclose(summary.shrink_themes.expected_return_m, -2.0, abs_tol=1e-4)
    assert math.isclose(summary.shrink_themes.share_of_expected_return_pct, expected_shrink_pct, abs_tol=1e-2)
    assert math.isclose(summary.shrink_themes.top_cedents[0].share_of_expected_return_pct, expected_shrink_pct, abs_tol=1e-2)
    assert math.isclose(summary.shrink_themes.top_regions[0].share_of_expected_return_pct, expected_shrink_pct, abs_tol=1e-2)
    assert math.isclose(summary.shrink_themes.top_perils[0].share_of_expected_return_pct, expected_shrink_pct, abs_tol=1e-2)


def test_explore_endpoint_returns_exact_deal_stability():
    """End-to-end /api/optimizer/explore returns dealStability matching the solver's participation vectors."""
    resp = client.post(
        "/api/optimizer/explore?num_points=8",
        json={
            "mode": "FORWARD_PLANNING",
            "granularity": "LAYER",
            "totalCapital": 450.0,
            "maxDeployedCollateral": 450.0,
            "maxTvar100M": 95.0,
            "maxTvar250M": 115.0,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    stab = body["dealStability"]
    assert stab is not None

    feasible_pts = [p for p in body["frontier"] if p["status"] in ("Optimal", "Feasible")]
    assert stab["feasiblePointCount"] == len(feasible_pts)
    assert stab["totalPointCount"] == len(body["frontier"])
    assert len(stab["deals"]) == len(body["initialLayers"])
    assert (
        stab["consistentGrowCount"] + stab["consistentShrinkCount"] + stab["unstableCount"]
        == len(stab["deals"])
    )

    # Independently verify every deal's min/median/max/std/iqr against the raw frontier participation vectors
    for row in stab["deals"]:
        k = row["layerKey"]
        ws = np.array(
            [next(l["optimalShare"] for l in pt["layers"] if l["layerKey"] == k) for pt in feasible_pts],
            dtype=np.float64,
        )
        assert math.isclose(row["minShare"], float(np.min(ws)), abs_tol=1e-3)
        assert math.isclose(row["medianShare"], float(np.median(ws)), abs_tol=1e-3)
        assert math.isclose(row["maxShare"], float(np.max(ws)), abs_tol=1e-3)
        assert math.isclose(row["stdShare"], float(np.std(ws)), abs_tol=1e-3)
        q1, q3 = np.percentile(ws, [25.0, 75.0])
        assert math.isclose(row["iqrShare"], float(q3 - q1), abs_tol=1e-3)

    # Theme counts must match bucket counts
    assert stab["growThemes"]["dealCount"] == stab["consistentGrowCount"]
    assert stab["shrinkThemes"]["dealCount"] == stab["consistentShrinkCount"]
