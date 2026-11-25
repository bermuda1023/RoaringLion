"""Phase 2 Verification Suite: Extended DAG terms, signed/weighted Combination, Node Cache, Graph Versioning & Bind."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.engine.slice_runner import clear_dag_cache
from app.main import app


def test_phase2_dag_cache_hits_on_downstream_edit():
    """Editing a downstream OccXL node hits cache on unchanged upstream LossSource & Filter nodes."""
    client = TestClient(app)
    clear_dag_cache()

    r1 = client.post("/api/programmes/prog-farmers-us-27/evaluate")
    assert r1.status_code == 200
    assert r1.json()["cacheMisses"] > 0
    adj = {n["nodeId"]: n for n in r1.json()["views"]["ADJUSTED"]}
    assert adj["lyr-occ-1"]["elr"] is not None and 0 < adj["lyr-occ-1"]["elr"] < 1
    assert abs(adj["out-pricing-occ"]["elr"] - adj["lyr-occ-1"]["elr"]) < 1e-3

    # Immediate re-evaluate hits 100% cache
    r2 = client.post("/api/programmes/prog-farmers-us-27/evaluate")
    assert r2.status_code == 200
    assert r2.json()["cacheMisses"] == 0
    assert r2.json()["cacheHits"] >= 12  # 6 nodes x 2 views

    # Edit downstream lyr-occ-1 attachment -> upstream src-1 and flt-us-wind stay cached
    prog = client.get("/api/programmes/prog-farmers-us-27").json()
    graph = prog["graph"]
    for n in graph["nodes"]:
        if n["nodeId"] == "lyr-occ-1":
            n["params"]["occAttachment"] = 85.0
    r_upd = client.put("/api/programmes/prog-farmers-us-27/graph", json=graph)
    assert r_upd.status_code == 200
    assert r_upd.json()["graph"]["version"] == prog["graph"]["version"] + 1

    r3 = client.post("/api/programmes/prog-farmers-us-27/evaluate")
    assert r3.status_code == 200
    assert r3.json()["cacheHits"] >= 4  # src-1, flt-us-wind, lyr-agg-1, out-yelt-agg across both views
    assert r3.json()["cacheMisses"] >= 2


def test_phase2_cycle_rejection_and_extended_nodes_and_bind():
    """Reject cyclic graphs; evaluate QuotaShare, EventOrderFilter, subtractive Combination, Cascading; bind deal."""
    client = TestClient(app)

    # Create a custom contract to test extended nodes
    r_c = client.post(
        "/api/contracts",
        json={
            "cedentName": "Pillar Retro Ltd",
            "programmeName": "Complex Multi-Node Tower",
            "treatyYear": 2027,
            "renewalPeriod": "J27",
            "office": "BDA",
            "underwriter": "Lead Underwriter",
            "broker": "Aon",
            "status": "QUOTED",
            "limit": 70.0,
            "attachment": 50.0,
            "depositPremium": 14.0,
            "rol": 0.20,
            "collateralRequired": 70.0,
            "regionPerils": ["US-FL-WS", "US-TX-WS"],
            "perils": ["WS"],
        },
    )
    assert r_c.status_code == 200
    pid = r_c.json()["programmeId"]
    gid = r_c.json()["graph"]["graphId"]

    # 1. Cyclic graph must return 422
    cyclic = {
        "graphId": gid,
        "name": "Bad Cycle",
        "version": 1,
        "currency": "USD",
        "nodes": [
            {"nodeId": "a", "nodeType": "COMBINATION", "label": "A", "params": {}, "position": {"x": 0, "y": 0}},
            {"nodeId": "b", "nodeType": "COMBINATION", "label": "B", "params": {}, "position": {"x": 100, "y": 0}},
        ],
        "edges": [
            {"edgeId": "e1", "source": "a", "target": "b", "sign": 1, "weight": 1.0},
            {"edgeId": "e2", "source": "b", "target": "a", "sign": 1, "weight": 1.0},
        ],
    }
    assert client.put(f"/api/programmes/{pid}/graph", json=cyclic).status_code == 422

    # 2. Valid extended DAG: LossSource -> RegionPerilFilter -> EventOrder(2nd+) + QuotaShare + Subtractive Inuring Combination + CascadingTower
    valid_graph = {
        "graphId": gid,
        "name": "Extended Phase 2 DAG",
        "version": 1,
        "currency": "USD",
        "nodes": [
            {"nodeId": "src", "nodeType": "LOSS_SOURCE", "label": "Source", "params": {}, "position": {"x": 0, "y": 100}},
            {"nodeId": "flt", "nodeType": "REGION_PERIL_FILTER", "label": "US FL/TX", "params": {"regionPerils": ["US-FL-WS", "US-TX-WS"]}, "position": {"x": 200, "y": 100}},
            {"nodeId": "qs", "nodeType": "AGG_QUOTA_SHARE", "label": "25% Inuring QS", "params": {"cessionShare": 0.25, "eventCap": 30.0, "aggCap": 50.0, "placedShare": 1.0}, "position": {"x": 400, "y": 20}},
            {"nodeId": "net", "nodeType": "COMBINATION", "label": "Net of QS (Gross - QS)", "params": {}, "position": {"x": 620, "y": 100}},
            {"nodeId": "eo2", "nodeType": "EVENT_ORDER_FILTER", "label": "2nd+ Event Only", "params": {"minOrder": 2, "minLossThreshold": 10.0}, "position": {"x": 620, "y": 240}},
            {
                "nodeId": "casc",
                "nodeType": "CASCADING_TOWER",
                "label": "Cascading Drop-Down",
                "params": {
                    "occLimit": 70.0,
                    "depositPremium": 14.0,
                    "placedShare": 1.0,
                    "layers": [
                        {"occLimit": 30.0, "occAttachment": 50.0, "reinstatements": 0.0, "ripRate": 0.0, "depositPremium": 8.0, "placedShare": 1.0},
                        {"occLimit": 40.0, "occAttachment": 80.0, "reinstatements": 0.0, "ripRate": 0.0, "depositPremium": 6.0, "placedShare": 1.0},
                    ],
                },
                "position": {"x": 850, "y": 100},
            },
        ],
        "edges": [
            {"edgeId": "e1", "source": "src", "target": "flt", "sign": 1, "weight": 1.0},
            {"edgeId": "e2", "source": "flt", "target": "qs", "sign": 1, "weight": 1.0},
            {"edgeId": "e3", "source": "flt", "target": "net", "sign": 1, "weight": 1.0},
            {"edgeId": "e4", "source": "qs", "target": "net", "sign": -1, "weight": 1.0},  # dashed subtractive inuring!
            {"edgeId": "e5", "source": "flt", "target": "eo2", "sign": 1, "weight": 1.0},
            {"edgeId": "e6", "source": "net", "target": "casc", "sign": 1, "weight": 1.0},
        ],
    }
    assert client.put(f"/api/programmes/{pid}/graph", json=valid_graph).status_code == 200
    r_eval = client.post(f"/api/programmes/{pid}/evaluate")
    assert r_eval.status_code == 200
    unadj = {m["nodeId"]: m for m in r_eval.json()["views"]["UNADJUSTED"]}
    # Net of QS EL must be strictly less than Gross Filter EL
    assert unadj["net"]["el"] < unadj["flt"]["el"]
    assert abs((unadj["flt"]["el"] - unadj["qs"]["el"]) - unadj["net"]["el"]) < 0.01
    # 2nd+ event EL must be strictly less than all-events EL
    assert unadj["eo2"]["el"] < unadj["flt"]["el"]
    assert unadj["casc"]["el"] > 0.0

    # 3. Bind Deal Snapshot
    r_bind = client.post(f"/api/programmes/{pid}/bind")
    assert r_bind.status_code == 200
    assert r_bind.json()["contract"]["status"] == "BOUND"
    assert r_bind.json()["bindManifestId"].startswith("man-deal_bind-")


def test_graph_templates_blank_canvas_and_cms_suggestions():
    client = TestClient(app)
    r_blank = client.get("/api/graph-templates")
    assert r_blank.status_code == 200
    blank_occ = next(t for t in r_blank.json() if t["templateId"] == "blank-occ-xl")
    assert blank_occ["label"] == "Occurrence XL"
    assert blank_occ["params"]["occLimit"] == ""
    assert blank_occ["params"]["occAttachment"] == ""

    r_cms = client.get("/api/graph-templates?programmeId=prog-farmers-us-27")
    assert r_cms.status_code == 200
    cms_items = [t for t in r_cms.json() if t.get("source") == "CMS"]
    assert any(t["templateId"] == "cms-layer-prog-farmers-us-27" for t in cms_items)
    assert any(t["templateId"] == "cms-perils-prog-farmers-us-27" for t in cms_items)
    assert any("Prior UY (2026)" in t["category"] for t in cms_items)


def test_zone_and_peril_filter_combinations():
    from app.engine.slice_runner import _allowed_rp_keys_for_filter

    assert _allowed_rp_keys_for_filter({}) is None
    assert _allowed_rp_keys_for_filter({"perils": ["EQ"]}) == {"US-CA-EQ", "JP-KANTO-EQ"}
    assert _allowed_rp_keys_for_filter({"zones": ["US-FL", "US-TX"]}) == {"US-FL-WS", "US-TX-WS"}
    assert _allowed_rp_keys_for_filter({"zones": ["US"], "perils": ["WS"]}) == {"US-FL-WS", "US-TX-WS", "US-SE-WS"}
    assert _allowed_rp_keys_for_filter({"zones": ["US"], "perils": ["EQ"]}) == {"US-CA-EQ"}
    assert _allowed_rp_keys_for_filter({"regionPerils": ["EU-DE-WS"], "perils": ["EQ"]}) == {"EU-DE-WS", "US-CA-EQ", "JP-KANTO-EQ"}
