"""Phase 0 Kernel & Solver Benchmark Gate.

Compares:
1. Compiled Rust kernel (PyO3 + Rayon) vs pure Python stateful loop on 100k trials.
2. HiGHS (highspy) LP and MILP (semi-continuous min-line + Rockafellar-Uryasev CVaR tail constraint)
   on a 100-layer portfolio with tail-scenario reduction.

Writes JSON results to `bench/results.json` and prints a concise summary.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import highspy
import numpy as np
import roaring_lion_engine as rle


def python_occ_xl_loop(trial_ids: np.ndarray, losses: np.ndarray, num_trials: int, limit: float, att: float, reinst: float) -> np.ndarray:
    annual_cap = (1.0 + reinst) * limit
    ann = np.zeros(num_trials, dtype=np.float64)
    n = len(trial_ids)
    i = 0
    while i < n:
        t = int(trial_ids[i]) - 1
        cum = 0.0
        while i < n and int(trial_ids[i]) - 1 == t:
            g = float(losses[i])
            raw = min(max(g - att, 0.0), limit)
            in_layer = min(raw, max(annual_cap - cum, 0.0))
            cum += in_layer
            i += 1
        ann[t] = cum
    return ann


def benchmark_highs(num_layers: int = 100, num_trials: int = 100_000, tail_scenarios: int = 2000) -> dict:
    rng = np.random.default_rng(42)
    # Synthetic layer annual loss matrix (M x N) in $M
    # Each layer has 50M limit, expected profit ~ 4M..9M
    limits = np.full(num_layers, 50.0)
    premiums = rng.uniform(5.0, 12.0, size=num_layers)
    # Tail-heavy correlated loss matrix on tail_scenarios (representing top 2% worst trials out of 100k)
    common_shock = rng.pareto(2.5, size=tail_scenarios)
    layer_losses = np.minimum(
        limits[:, None],
        np.maximum(0.0, (common_shock[None, :] * rng.uniform(0.3, 1.5, size=(num_layers, 1)) - 0.8) * 25.0),
    )
    expected_losses = rng.uniform(2.0, 6.0, size=num_layers)
    expected_profit = premiums - expected_losses

    # --- 1. Continuous LP with Rockafellar-Uryasev CVaR (1-in-100) + Capital budget ---
    t0 = time.perf_counter()
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    # Variables: w_0..w_{M-1} in [0, 1], alpha (VaR threshold >= 0), z_0..z_{S-1} >= 0
    n_vars = num_layers + 1 + tail_scenarios
    alpha_idx = num_layers
    z_start = num_layers + 1

    # Objective: maximize sum(expected_profit_i * w_i) -> minimize -expected_profit_i * w_i
    for i in range(num_layers):
        h.addVar(0.0, 1.0)
        h.changeColCost(i, -float(expected_profit[i]))
    h.addVar(0.0, highspy.kHighsInf)  # alpha
    for s in range(tail_scenarios):
        h.addVar(0.0, highspy.kHighsInf)  # z_s

    # Capital budget: sum(limits_i * w_i) <= 1000.0 ($1B deployed capital)
    h.addRow(0.0, 1000.0, num_layers, list(range(num_layers)), limits.tolist())

    # Tail CVaR constraint at 1-in-100 across 100k trials (p = 0.01 => 1000 tail trials)
    # alpha + (1 / (0.01 * N)) * sum(z_s) <= 450.0 ($450M max 1-in-100 TVaR)
    inv_tail = 1.0 / (0.01 * num_trials)
    cvar_idx = [alpha_idx] + list(range(z_start, z_start + tail_scenarios))
    cvar_val = [1.0] + [inv_tail] * tail_scenarios
    h.addRow(-highspy.kHighsInf, 450.0, len(cvar_idx), cvar_idx, cvar_val)

    # Scenario epigraph rows: sum(L_{i,s} * w_i) - alpha - z_s <= 0
    for s in range(tail_scenarios):
        nz = np.nonzero(layer_losses[:, s] > 0.0)[0]
        row_idx = nz.tolist() + [alpha_idx, z_start + s]
        row_val = layer_losses[nz, s].tolist() + [-1.0, -1.0]
        h.addRow(-highspy.kHighsInf, 0.0, len(row_idx), row_idx, row_val)

    h.run()
    lp_ms = (time.perf_counter() - t0) * 1000.0
    lp_status = h.modelStatusToString(h.getModelStatus())
    lp_obj = -h.getObjectiveValue()

    # --- 2. MILP with semi-continuous minimum line size (w_i = 0 OR w_i >= 0.15) ---
    t1 = time.perf_counter()
    hm = highspy.Highs()
    hm.setOptionValue("output_flag", False)
    hm.setOptionValue("time_limit", 10.0)
    hm.setOptionValue("mip_rel_gap", 0.01)
    # Use 500 top tail scenarios for MILP
    s_milp = 500
    for i in range(num_layers):
        hm.addVar(0.0, 1.0)
        hm.changeColCost(i, -float(expected_profit[i]))
    y_start = num_layers
    for i in range(num_layers):
        hm.addVar(0.0, 1.0)
        hm.changeColIntegrality(y_start + i, highspy.HighsVarType.kInteger)
    m_alpha = y_start + num_layers
    hm.addVar(0.0, highspy.kHighsInf)
    m_z_start = m_alpha + 1
    for _ in range(s_milp):
        hm.addVar(0.0, highspy.kHighsInf)

    # Semi-continuous linking: 0.15 * y_i <= w_i <= 1.0 * y_i
    for i in range(num_layers):
        hm.addRow(-highspy.kHighsInf, 0.0, 2, [i, y_start + i], [1.0, -1.0])
        hm.addRow(-highspy.kHighsInf, 0.0, 2, [i, y_start + i], [-1.0, 0.15])

    # Capital budget
    hm.addRow(0.0, 1000.0, num_layers, list(range(num_layers)), limits.tolist())
    # CVaR constraint
    cvar_idx_m = [m_alpha] + list(range(m_z_start, m_z_start + s_milp))
    cvar_val_m = [1.0] + [1.0 / (0.005 * num_trials)] * s_milp
    hm.addRow(-highspy.kHighsInf, 450.0, len(cvar_idx_m), cvar_idx_m, cvar_val_m)
    for s in range(s_milp):
        nz = np.nonzero(layer_losses[:, s] > 0.0)[0]
        row_idx = nz.tolist() + [m_alpha, m_z_start + s]
        row_val = layer_losses[nz, s].tolist() + [-1.0, -1.0]
        hm.addRow(-highspy.kHighsInf, 0.0, len(row_idx), row_idx, row_val)

    hm.run()
    milp_ms = (time.perf_counter() - t1) * 1000.0
    milp_status = hm.modelStatusToString(hm.getModelStatus())
    milp_obj = -hm.getObjectiveValue()

    return {
        "solver": "HiGHS (highspy)",
        "num_layers": num_layers,
        "num_trials_represented": num_trials,
        "lp_tail_scenarios": tail_scenarios,
        "lp_ms": round(lp_ms, 2),
        "lp_status": lp_status,
        "lp_expected_profit_m": round(lp_obj, 3),
        "milp_tail_scenarios": s_milp,
        "milp_min_line_ms": round(milp_ms, 2),
        "milp_status": milp_status,
        "milp_expected_profit_m": round(milp_obj, 3),
    }


def main() -> None:
    num_trials = 100_000
    events_per_trial = 3
    rng = np.random.default_rng(12345)
    trial_ids = np.repeat(np.arange(1, num_trials + 1, dtype=np.uint32), events_per_trial)
    losses = 10.0 * (1.0 - rng.uniform(0.0, 0.999999, size=num_trials * events_per_trial)) ** (-0.45)

    occ_params = json.dumps({
        "occ_limit": 50.0,
        "occ_attachment": 100.0,
        "occ_franchise": 0.0,
        "reinstatements": 1.0,
        "rip_rate": 1.0,
        "deposit_premium": 8.5,
        "placed_share": 1.0,
    })
    agg_params = json.dumps({
        "agg_limit": 40.0,
        "agg_attachment": 60.0,
        "per_event_franchise": 5.0,
        "per_event_cap": 50.0,
        "placed_share": 1.0,
    })

    # Warm Rust PyO3 call
    _ = rle.eval_occ_xl_yelt(trial_ids, losses, num_trials, occ_params)

    t0 = time.perf_counter()
    _, ann_rust, _, _ = rle.eval_occ_xl_yelt(trial_ids, losses, num_trials, occ_params)
    rust_occ_ms = (time.perf_counter() - t0) * 1000.0

    t1 = time.perf_counter()
    _, ann_agg_rust, _ = rle.eval_agg_xl_yelt(trial_ids, losses, num_trials, agg_params)
    rust_agg_ms = (time.perf_counter() - t1) * 1000.0

    t2 = time.perf_counter()
    ann_py = python_occ_xl_loop(trial_ids, losses, num_trials, 50.0, 100.0, 1.0)
    py_occ_ms = (time.perf_counter() - t2) * 1000.0

    max_diff = float(np.max(np.abs(ann_rust - ann_py)))
    assert max_diff < 1e-9, f"Rust vs Python mismatch: {max_diff}"

    # 500-layer book via PyO3
    t3 = time.perf_counter()
    for i in range(250):
        p = json.dumps({
            "occ_limit": 50.0,
            "occ_attachment": 80.0 + i * 0.5,
            "occ_franchise": 0.0,
            "reinstatements": 1.0,
            "rip_rate": 1.0,
            "deposit_premium": 8.5,
            "placed_share": 1.0,
        })
        rle.eval_occ_xl_yelt(trial_ids, losses, num_trials, p)
    for i in range(250):
        p = json.dumps({
            "agg_limit": 40.0,
            "agg_attachment": 50.0 + i * 0.4,
            "per_event_franchise": 5.0,
            "per_event_cap": 50.0,
            "placed_share": 1.0,
        })
        rle.eval_agg_xl_yelt(trial_ids, losses, num_trials, p)
    rust_book_500_ms = (time.perf_counter() - t3) * 1000.0

    solver_bench = benchmark_highs()

    results = {
        "kernel_benchmark": {
            "num_trials": num_trials,
            "total_events": int(len(losses)),
            "rust_pyo3_single_occ_xl_ms": round(rust_occ_ms, 2),
            "rust_pyo3_single_agg_xl_ms": round(rust_agg_ms, 2),
            "python_loop_single_occ_xl_ms": round(py_occ_ms, 2),
            "speedup_rust_vs_python": round(py_occ_ms / max(rust_occ_ms, 0.01), 1),
            "rust_pyo3_500_layers_ms": round(rust_book_500_ms, 2),
            "max_numerical_diff_rust_vs_python": max_diff,
            "julia_status": "not_installed_on_vm_rust_passed_gate",
        },
        "solver_benchmark": solver_bench,
    }

    out_path = Path(__file__).parent / "results.json"
    out_path.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
