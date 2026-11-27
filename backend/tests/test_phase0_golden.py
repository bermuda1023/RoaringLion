"""Phase 0 Golden, Closed-Form, Parity, Conservation & API Verification Suite."""

from __future__ import annotations

import json

import numpy as np
import pytest
import roaring_lion_engine as rle
from fastapi.testclient import TestClient

from app.engine.sim_generator import REGION_PERILS, analytic_pareto_occ_xl_el, generate_synthetic_sim_set
from app.engine.slice_runner import (
    evaluate_deterministic_run,
    evaluate_portfolio_rollup,
    evaluate_programme_dag,
)
from app.main import PROGRAMMES, SIM_SETS, app
from app.models.domain import DeterministicLossRow, DeterministicRunRequest, UpliftRule
from app.models.enums import ViewOfRisk
from app.services.lineage import LineageRegistry


def test_closed_form_pareto_poisson_el():
    """Golden test: 100k-trial Rust OccXL EL matches exact closed-form Pareto-Poisson integral."""
    lam = 0.8
    alpha = 2.2
    xm = 20.0
    attachment = 40.0
    limit = 60.0
    num_trials = 100_000

    rng = np.random.default_rng(777)
    counts = rng.poisson(lam, size=num_trials)
    trial_ids = np.repeat(np.arange(1, num_trials + 1, dtype=np.uint32), counts)
    u = rng.uniform(0.0, 1.0, size=int(counts.sum()))
    losses = xm * (1.0 - u) ** (-1.0 / alpha)

    params_json = json.dumps({
        "occ_limit": limit,
        "occ_attachment": attachment,
        "occ_franchise": 0.0,
        "reinstatements": None,  # unlimited so analytic integral is exact
        "rip_rate": 0.0,
        "deposit_premium": 0.0,
        "placed_share": 1.0,
    })
    _, ann, _, _ = rle.eval_occ_xl_yelt(trial_ids, losses, num_trials, params_json)
    simulated_el = float(np.mean(ann))
    exact_el = analytic_pareto_occ_xl_el(lam, alpha, xm, attachment, limit)
    rel_err = abs(simulated_el - exact_el) / exact_el
    assert rel_err < 0.025, f"Simulated EL {simulated_el:.4f} vs analytic {exact_el:.4f} (err={rel_err:.4%})"


def test_deterministic_stochastic_parity():
    """Parity test: feeding a YELT trial's events as a deterministic loss list reproduces the stochastic kernel."""
    yelt = generate_synthetic_sim_set(num_trials=5_000, seed=99)
    prog = PROGRAMMES["prog-farmers-us-27"]
    lineage = LineageRegistry()

    _, vecs = evaluate_programme_dag(prog, yelt, lineage, views=[ViewOfRisk.UNADJUSTED])
    ann_occ = vecs["annual"]["UNADJUSTED"]["lyr-occ-1"]
    ann_agg = vecs["annual"]["UNADJUSTED"]["lyr-agg-1"]
    rip_occ = vecs["rip"]["UNADJUSTED"]["lyr-occ-1"]

    # Pick the trial with the highest combined loss so both OccXL (with reinstatement) and AggXL trigger
    worst_trial_idx = int(np.argmax(ann_occ + ann_agg))
    worst_trial_id = worst_trial_idx + 1
    mask = yelt.trial_ids == worst_trial_id
    assert np.any(mask)

    det_rows = []
    for i, (ev_id, day, rp_id, loss) in enumerate(
        zip(yelt.event_ids[mask], yelt.day_of_year[mask], yelt.rp_ids[mask], yelt.losses[mask])
    ):
        det_rows.append(
            DeterministicLossRow(
                loss_id=f"ev-{ev_id}",
                event_name=f"Trial {worst_trial_id} Event {ev_id}",
                occurrence_datetime=f"2027-01-01T{i:02d}:00:00Z",  # preserves exact sorted order
                region_peril_key=REGION_PERILS[int(rp_id)],
                ground_up_loss=float(loss),
            )
        )

    det_resp = evaluate_deterministic_run(
        DeterministicRunRequest(programme_id=prog.programme_id, view=ViewOfRisk.UNADJUSTED, losses=det_rows),
        prog,
        lineage,
    )
    by_layer = {ls.layer_id: ls for ls in det_resp.layer_statements}
    assert abs(by_layer["lyr-occ-1"].total_ceded_loss - float(ann_occ[worst_trial_idx])) < 1e-3
    assert abs(by_layer["lyr-occ-1"].total_rip_due - float(rip_occ[worst_trial_idx])) < 1e-3
    assert abs(by_layer["lyr-agg-1"].total_ceded_loss - float(ann_agg[worst_trial_idx])) < 1e-3


def test_euler_cotvar_conservation_and_uplift_separation():
    """Conservation: sum(Co-TVaR) == Portfolio TVaR. Uplift: ADJUSTED != UNADJUSTED while UNADJUSTED stays invariant."""
    yelt = next(iter(SIM_SETS.values()))
    lineage = LineageRegistry()
    bound = list(PROGRAMMES.values())

    unadj = evaluate_portfolio_rollup(
        portfolio_id="p-test",
        portfolio_name="Test Fund",
        programmes=bound,
        yelt=yelt,
        view=ViewOfRisk.UNADJUSTED,
        total_capital=160.0,
        lineage=lineage,
    )
    adj = evaluate_portfolio_rollup(
        portfolio_id="p-test",
        portfolio_name="Test Fund",
        programmes=bound,
        yelt=yelt,
        view=ViewOfRisk.ADJUSTED,
        total_capital=160.0,
        lineage=lineage,
    )

    assert unadj.cotvar_sum_check_diff < 1e-9
    assert adj.cotvar_sum_check_diff < 1e-9
    # Positive severity uplifts must strictly increase expected loss on ADJUSTED vs UNADJUSTED
    assert adj.expected_loss > unadj.expected_loss
    assert unadj.manifest.output_hash != adj.manifest.output_hash


def test_api_first_slice_end_to_end():
    """End-to-end API verification of the Phase 0 First Slice and breadcrumb lineage."""
    client = TestClient(app)

    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json()["simSetsLoaded"] == 1

    r_eval = client.post("/api/programmes/prog-farmers-us-27/evaluate")
    assert r_eval.status_code == 200
    body = r_eval.json()
    assert "UNADJUSTED" in body["views"] and "ADJUSTED" in body["views"]
    manifest_id = body["manifest"]["manifestId"]

    # Breadcrumb manifest must be retrievable
    r_man = client.get(f"/api/lineage/manifests/{manifest_id}")
    assert r_man.status_code == 200
    assert len(r_man.json()["breadcrumb"]) >= 4

    # Portfolio rollup (with bound hedges and long-only without hedges)
    r_port = client.get("/api/portfolio/rollup?view=ADJUSTED&totalCapital=160")
    assert r_port.status_code == 200
    pbody = r_port.json()
    assert pbody["cotvarSumCheckDiff"] < 1e-9
    assert len(pbody["cdfCurve"]) > 20
    assert len([r for r in pbody["layerAllocations"] if not r["isHedge"]]) == 4

    r_gross = client.get("/api/portfolio/rollup?view=ADJUSTED&totalCapital=160&includeHedges=false")
    assert r_gross.status_code == 200
    assert len(r_gross.json()["layerAllocations"]) == 4

    # Deterministic run
    r_det = client.post(
        "/api/deterministic/run",
        json={
            "programmeId": "prog-farmers-us-27",
            "view": "UNADJUSTED",
            "hoursClause": 72,
            "losses": [
                {
                    "lossId": "L1",
                    "eventName": "Hurricane Alpha (FL)",
                    "occurrenceDatetime": "2027-08-22T12:00:00Z",
                    "regionPerilKey": "US-FL-WS",
                    "groundUpLoss": 115.0,
                },
                {
                    "lossId": "L2",
                    "eventName": "Hurricane Beta (TX)",
                    "occurrenceDatetime": "2027-09-14T18:00:00Z",
                    "regionPerilKey": "US-TX-WS",
                    "groundUpLoss": 140.0,
                },
            ],
        },
    )
    assert r_det.status_code == 200
    dbody = r_det.json()
    # L1: 115 xs 80 -> 35 in 50m OccXL; L2: 140 xs 80 -> 50 in OccXL (total 85m, 35m reinstated)
    occ_stmt = next(s for s in dbody["layerStatements"] if s["layerId"] == "lyr-occ-1")
    assert abs(occ_stmt["totalCededLoss"] - 85.0) < 1e-6
    assert occ_stmt["steps"][0]["stateAfter"]["reinstatementsUsed"] == pytest.approx(0.7, abs=1e-4)
    assert occ_stmt["steps"][1]["stateAfter"]["reinstatementsUsed"] == pytest.approx(1.0, abs=1e-4)
