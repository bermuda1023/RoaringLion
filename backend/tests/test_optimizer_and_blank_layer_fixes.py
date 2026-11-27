"""Regression tests for:
1. Optimizer endpoints (CORS on errors, UNL retro snapshot, blank-premium layers, warm-start frontier, concurrency).
2. Creating & running fresh layers from empty nodes without depositPremium (standalone, YELT_OUTPUT, unlinked PRICING_OUTPUT).
"""

from __future__ import annotations

import concurrent.futures
import copy

from fastapi.testclient import TestClient

from app.main import CONTRACT_STORE, app

client = TestClient(app)


def _create_temp_programme(name: str = "Blank Layer Test") -> dict:
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": "BlankLayer Regression Re",
            "programmeName": name,
            "status": "QUOTED",
            "limit": 30.0,
            "attachment": 60.0,
            "depositPremium": 4.0,
            "signedLine": 1.0,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _park_declined(programme_id: str) -> None:
    client.put(f"/api/contracts/{programme_id}", json={"status": "DECLINED"})


def test_blank_layer_templates_omit_deposit_premium():
    res = client.get("/api/graph-templates")
    assert res.status_code == 200
    blank_layers = [
        t
        for t in res.json()
        if t["source"] == "BLANK"
        and t["nodeType"]
        in {"OCC_XL", "AGG_XL", "AGG_QUOTA_SHARE", "QUOTA_SHARE", "SURPLUS_SHARE", "CASCADING_TOWER", "ILW_OPTION"}
    ]
    assert len(blank_layers) >= 5
    for tpl in blank_layers:
        assert "depositPremium" not in tpl["params"], f"{tpl['templateId']} should not require depositPremium"


def test_fresh_occ_xl_layer_runs_without_deposit_premium_and_returns_el_via_yelt_pricing_and_standalone():
    created = _create_temp_programme("Fresh Occ XL No Premium")
    pid = created["programmeId"]
    try:
        src_id = next(n["nodeId"] for n in created["graph"]["nodes"] if n["nodeType"] == "LOSS_SOURCE")
        # Test all three representations of missing depositPremium: omitted, None, and ""
        for idx, dp_variant in enumerate(({}, {"depositPremium": None}, {"depositPremium": ""})):
            layer_id = f"lyr-blank-occ-{idx}"
            yelt_id = f"out-yelt-blank-{idx}"
            px_id = f"out-px-blank-{idx}"
            graph = {
                "graphId": created["graph"]["graphId"],
                "name": created["graph"]["name"],
                "version": created["graph"]["version"],
                "currency": "USD",
                "nodes": [
                    {
                        "nodeId": src_id,
                        "nodeType": "LOSS_SOURCE",
                        "label": "Loss Source",
                        "params": {"simSetId": created["simSetId"]},
                        "position": {"x": 40, "y": 100},
                    },
                    {
                        "nodeId": layer_id,
                        "nodeType": "OCC_XL",
                        "label": "Fresh Occ XL",
                        "params": {
                            "layerCode": f"L{idx + 1}",
                            "occLimit": 40.0,
                            "occAttachment": 60.0,
                            "occFranchise": 0.0,
                            "reinstatements": 0.0,
                            "ripRate": 0.0,
                            "placedShare": 1.0,
                            **dp_variant,
                        },
                        "position": {"x": 360, "y": 100},
                    },
                    {
                        "nodeId": yelt_id,
                        "nodeType": "YELT_OUTPUT",
                        "label": "YELT Inspector",
                        "params": {"view": "ADJUSTED"},
                        "position": {"x": 680, "y": 60},
                    },
                    {
                        "nodeId": px_id,
                        "nodeType": "PRICING_OUTPUT",
                        "label": "Canvas Pricing",
                        "params": {"unlinked": True},
                        "position": {"x": 680, "y": 180},
                    },
                ],
                "edges": [
                    {"edgeId": f"e-src-{idx}", "source": src_id, "target": layer_id, "sign": 1, "weight": 1.0},
                    {"edgeId": f"e-yelt-{idx}", "source": layer_id, "target": yelt_id, "sign": 1, "weight": 1.0},
                    {"edgeId": f"e-px-{idx}", "source": layer_id, "target": px_id, "sign": 1, "weight": 1.0},
                ],
            }
            r_save = client.put(f"/api/programmes/{pid}/graph", json=graph)
            assert r_save.status_code == 200, r_save.text

            r_eval = client.post(f"/api/programmes/{pid}/evaluate")
            assert r_eval.status_code == 200, r_eval.text
            adj = {m["nodeId"]: m for m in r_eval.json()["views"]["ADJUSTED"]}
            assert adj[layer_id]["el"] > 0.0
            assert adj[layer_id]["rol"] is None
            assert adj[layer_id]["elr"] is None
            assert adj[layer_id]["expectedRip"] == 0.0
            # YELT_OUTPUT inspects the layer loss stream directly
            assert abs(adj[yelt_id]["el"] - adj[layer_id]["el"]) < 1e-4
            assert adj[yelt_id]["rol"] is None
            assert adj[yelt_id]["elr"] is None
            # Unlinked PRICING_OUTPUT also evaluates cleanly without premium
            assert abs(adj[px_id]["el"] - adj[layer_id]["el"]) < 1e-4
            assert adj[px_id]["elr"] is None

            r_yelt = client.get(f"/api/programmes/{pid}/nodes/{yelt_id}/yelt?view=ADJUSTED")
            assert r_yelt.status_code == 200, r_yelt.text
            assert abs(r_yelt.json()["aal"] - adj[layer_id]["el"]) < 1e-3

        # Deterministic run also succeeds cleanly with blank/missing depositPremium
        r_det = client.post(
            "/api/deterministic/run",
            json={
                "programmeId": pid,
                "view": "ADJUSTED",
                "hoursClause": 72,
                "losses": [
                    {
                        "lossId": "E1",
                        "eventName": "Hurricane Test",
                        "occurrenceDatetime": "2027-08-22T12:00:00Z",
                        "regionPerilKey": "US-FL-WS",
                        "groundUpLoss": 90.0,
                    }
                ],
            },
        )
        assert r_det.status_code == 200, r_det.text
        assert r_det.json()["totalCeded"] == 30.0
        assert r_det.json()["totalRipDue"] == 0.0
    finally:
        _park_declined(pid)


def test_invalid_deposit_premium_is_still_rejected():
    created = _create_temp_programme("Invalid DP Validation")
    pid = created["programmeId"]
    try:
        for bad_val in ("abc", -5.0):
            graph = copy.deepcopy(created["graph"])
            lyr = next(n for n in graph["nodes"] if n["nodeType"] == "OCC_XL")
            lyr["params"]["depositPremium"] = bad_val
            r = client.put(f"/api/programmes/{pid}/graph", json=graph)
            assert r.status_code == 422, f"Expected 422 for depositPremium={bad_val!r}, got {r.status_code}"
    finally:
        _park_declined(pid)


def test_blank_premium_on_all_layer_types_does_not_break_optimizer_endpoints():
    """Regression: previously saving a blank AGG_XL/QS/SURPLUS/ILW layer left depositPremium='' and crashed
    /api/optimizer/candidates and /api/optimizer/layers with an unhandled 500 (no CORS -> 'Failed to fetch')."""
    created = _create_temp_programme("All Blank Layer Types")
    pid = created["programmeId"]
    try:
        src_id = next(n["nodeId"] for n in created["graph"]["nodes"] if n["nodeType"] == "LOSS_SOURCE")
        graph = {
            "graphId": created["graph"]["graphId"],
            "name": created["graph"]["name"],
            "version": created["graph"]["version"],
            "currency": "USD",
            "nodes": [
                {
                    "nodeId": src_id,
                    "nodeType": "LOSS_SOURCE",
                    "label": "Loss Source",
                    "params": {"simSetId": created["simSetId"]},
                    "position": {"x": 40, "y": 100},
                },
                {
                    "nodeId": "lyr-agg-blank",
                    "nodeType": "AGG_XL",
                    "label": "Fresh Agg XL",
                    "params": {
                        "layerCode": "L1",
                        "aggLimit": 25.0,
                        "aggAttachment": 80.0,
                        "perEventFranchise": 0.0,
                        "perEventCap": "",
                        "depositPremium": "",
                        "placedShare": 1.0,
                    },
                    "position": {"x": 360, "y": 40},
                },
                {
                    "nodeId": "lyr-qs-blank",
                    "nodeType": "AGG_QUOTA_SHARE",
                    "label": "Fresh QS",
                    "params": {
                        "layerCode": "L2",
                        "cessionShare": 0.2,
                        "eventCap": "",
                        "aggCap": "",
                        "depositPremium": None,
                        "placedShare": 1.0,
                    },
                    "position": {"x": 360, "y": 160},
                },
                {
                    "nodeId": "lyr-surp-blank",
                    "nodeType": "SURPLUS_SHARE",
                    "label": "Fresh Surplus",
                    "params": {
                        "layerCode": "L3",
                        "retentionLine": 25.0,
                        "numLines": 3.0,
                        "placedShare": 1.0,
                    },
                    "position": {"x": 360, "y": 280},
                },
            ],
            "edges": [
                {"edgeId": "e1", "source": src_id, "target": "lyr-agg-blank", "sign": 1, "weight": 1.0},
                {"edgeId": "e2", "source": src_id, "target": "lyr-qs-blank", "sign": 1, "weight": 1.0},
                {"edgeId": "e3", "source": src_id, "target": "lyr-surp-blank", "sign": 1, "weight": 1.0},
            ],
        }
        r_save = client.put(f"/api/programmes/{pid}/graph", json=graph)
        assert r_save.status_code == 200, r_save.text

        r_eval = client.post(f"/api/programmes/{pid}/evaluate")
        assert r_eval.status_code == 200, r_eval.text
        adj = {m["nodeId"]: m for m in r_eval.json()["views"]["ADJUSTED"]}
        for lid in ("lyr-agg-blank", "lyr-qs-blank", "lyr-surp-blank"):
            assert adj[lid]["el"] > 0.0
            assert adj[lid]["rol"] is None
            assert adj[lid]["elr"] is None

        origin = {"Origin": "https://roaring-lion.vercel.app"}
        r_cand = client.get("/api/optimizer/candidates?view=ADJUSTED&mode=FORWARD_PLANNING&limit=25&offset=0", headers=origin)
        assert r_cand.status_code == 200, r_cand.text
        assert r_cand.headers.get("access-control-allow-origin") == "https://roaring-lion.vercel.app"

        r_layers = client.get("/api/optimizer/layers?view=ADJUSTED&mode=FORWARD_PLANNING", headers=origin)
        assert r_layers.status_code == 200, r_layers.text

        r_solve = client.post("/api/optimizer/solve", json={"mode": "FORWARD_PLANNING", "granularity": "LAYER"}, headers=origin)
        assert r_solve.status_code == 200, r_solve.text

        r_frontier = client.post("/api/optimizer/frontier?num_points=4", json={"mode": "FORWARD_PLANNING", "granularity": "LAYER"}, headers=origin)
        assert r_frontier.status_code == 200, r_frontier.text
        assert len(r_frontier.json()) == 4

        r_explore = client.post("/api/optimizer/explore?num_points=6", json={"mode": "FORWARD_PLANNING", "granularity": "LAYER"}, headers=origin)
        assert r_explore.status_code == 200, r_explore.text
        assert len(r_explore.json()["frontier"]) == 6
    finally:
        _park_declined(pid)


def test_optimizer_evaluates_unl_retro_hedge_against_portfolio_snapshot():
    """UNL retro hedge (`prog-retro-unl-27`) must evaluate against our bound UNL portfolio snapshot,
    respecting `asOfDate`, recording `PORTFOLIO_SNAPSHOT` lineage, and keeping portfolio initial EL positive."""
    r = client.post(
        "/api/optimizer/explore?num_points=6",
        json={"mode": "CURRENT", "granularity": "LAYER", "view": "ADJUSTED"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["initialPortfolio"]["expectedLossM"] > 0.0
    retro_rows = [l for l in body["initialLayers"] if l["programmeId"] == "prog-retro-unl-27"]
    assert retro_rows, "seeded UNL retro hedge prog-retro-unl-27 must appear in CURRENT optimizer layers"
    # UNL portfolio snapshot EL for 35m xs 45m retro is well below $1.5m (raw ground-up was ~$3.86m)
    assert retro_rows[0]["optimalElM"] < 1.5
    assert retro_rows[0]["coTvar250M"] <= 0.0
    assert any(k.startswith("portfolio_snapshot:") for k in body["manifest"]["inputHashes"])
    assert any(b["entityType"] == "PORTFOLIO_SNAPSHOT" for b in body["manifest"]["breadcrumb"])

    # Historical asOfDate with no contracts in force must match DAG evaluate (EL == 0.0)
    r_hist = client.post(
        "/api/contracts",
        json={
            "cedentName": "Hist Snapshot Re",
            "programmeName": "2020 Historical Retro",
            "status": "QUOTED",
            "direction": "EXTERNAL_RETRO",
            "structure": "OCC_XL",
            "instrument": "UNL",
            "portfolioSnapshotDate": "2020-01-01",
            "limit": 20.0,
            "attachment": 30.0,
            "depositPremium": 2.0,
            "signedLine": 1.0,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
        },
    )
    assert r_hist.status_code == 200, r_hist.text
    hist_id = r_hist.json()["programmeId"]
    try:
        r_eval = client.post(f"/api/programmes/{hist_id}/evaluate")
        assert r_eval.status_code == 200, r_eval.text
        eval_lyr = next(m for m in r_eval.json()["views"]["ADJUSTED"] if m["nodeType"] == "OCC_XL")
        assert eval_lyr["el"] == 0.0

        r_layers = client.get("/api/optimizer/layers?view=ADJUSTED&mode=CURRENT")
        assert r_layers.status_code == 200, r_layers.text
        opt_hist = next(l for l in r_layers.json() if l["programmeId"] == hist_id)
        assert opt_hist["el1xM"] == 0.0
    finally:
        _park_declined(hist_id)


def test_cascading_ilw_and_date_window_blank_params_evaluate_cleanly():
    created = _create_temp_programme("Cascading ILW DateWindow Blank")
    pid = created["programmeId"]
    try:
        src_id = next(n["nodeId"] for n in created["graph"]["nodes"] if n["nodeType"] == "LOSS_SOURCE")
        graph = {
            "graphId": created["graph"]["graphId"],
            "name": created["graph"]["name"],
            "version": created["graph"]["version"],
            "currency": "USD",
            "nodes": [
                {
                    "nodeId": src_id,
                    "nodeType": "LOSS_SOURCE",
                    "label": "Loss Source",
                    "params": {"simSetId": created["simSetId"]},
                    "position": {"x": 40, "y": 100},
                },
                {
                    "nodeId": "flt-date",
                    "nodeType": "DATE_WINDOW_FILTER",
                    "label": "Full Year",
                    "params": {"startDay": "", "endDay": None},
                    "position": {"x": 220, "y": 100},
                },
                {
                    "nodeId": "lyr-casc-blank",
                    "nodeType": "CASCADING_TOWER",
                    "label": "Fresh Tower",
                    "params": {
                        "layerCode": "L1",
                        "layers": [
                            {"occLimit": 30.0, "occAttachment": 60.0, "depositPremium": "", "placedShare": ""},
                        ],
                    },
                    "position": {"x": 460, "y": 60},
                },
                {
                    "nodeId": "lyr-ilw-blank",
                    "nodeType": "ILW_OPTION",
                    "label": "Fresh ILW",
                    "params": {
                        "layerCode": "L2",
                        "limit": 25.0,
                        "trigger": 40.0,
                        "triggerType": "BINARY",
                        "depositPremium": "",
                        "placedShare": 1.0,
                    },
                    "position": {"x": 460, "y": 200},
                },
            ],
            "edges": [
                {"edgeId": "e1", "source": src_id, "target": "flt-date", "sign": 1, "weight": 1.0},
                {"edgeId": "e2", "source": "flt-date", "target": "lyr-casc-blank", "sign": 1, "weight": 1.0},
                {"edgeId": "e3", "source": "flt-date", "target": "lyr-ilw-blank", "sign": 1, "weight": 1.0},
            ],
        }
        r_save = client.put(f"/api/programmes/{pid}/graph", json=graph)
        assert r_save.status_code == 200, r_save.text
        r_eval = client.post(f"/api/programmes/{pid}/evaluate")
        assert r_eval.status_code == 200, r_eval.text
        adj = {m["nodeId"]: m for m in r_eval.json()["views"]["ADJUSTED"]}
        assert adj["lyr-casc-blank"]["el"] > 0.0
        assert adj["lyr-casc-blank"]["rol"] is None
        assert adj["lyr-casc-blank"]["elr"] is None
        assert adj["lyr-ilw-blank"]["rol"] is None
        assert adj["lyr-ilw-blank"]["elr"] is None
    finally:
        _park_declined(pid)


def test_unhandled_500_includes_cors_headers(monkeypatch):
    """Cross-origin clients (Vercel -> Render) must receive CORS headers even on unexpected 500s
    so the browser surfaces the error detail rather than 'Failed to fetch'."""
    no_raise_client = TestClient(app, raise_server_exceptions=False)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated backend failure")

    monkeypatch.setattr(CONTRACT_STORE, "count_by_status", _boom)
    r = no_raise_client.get(
        "/api/optimizer/candidates?view=ADJUSTED&mode=FORWARD_PLANNING",
        headers={"Origin": "https://roaring-lion.vercel.app"},
    )
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == "https://roaring-lion.vercel.app"
    assert r.headers.get("access-control-allow-credentials") == "true"
    assert "simulated backend failure" in r.json()["detail"]


def test_concurrent_optimizer_candidates_and_explore_are_thread_safe():
    def _run(kind: str) -> int:
        if kind == "cand":
            return client.get("/api/optimizer/candidates?view=ADJUSTED&mode=FORWARD_PLANNING&limit=25&offset=0").status_code
        return client.post(
            "/api/optimizer/explore?num_points=6",
            json={"mode": "FORWARD_PLANNING", "granularity": "LAYER"},
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_run, ["cand", "explore", "cand", "explore"]))
    assert results == [200, 200, 200, 200]
