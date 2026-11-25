"""Smart node labels: terms regenerate from params, names and custom labels are preserved."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.node_labels import name_from_label, node_terms, smart_label

client = TestClient(app)


def test_terms_per_node_type():
    assert node_terms("OCC_XL", {"occLimit": 50, "occAttachment": 80, "reinstatements": 1, "ripRate": 1.0}) == "50m xs 80m (1@100%)"
    assert node_terms("OCC_XL", {"occLimit": 12.5, "occAttachment": 1500, "placedShare": 0.25}) == "12.5m xs 1.5bn · 25% share"
    assert node_terms("AGG_XL", {"aggLimit": 35, "aggAttachment": 95, "placedShare": 1.0}) == "35m xs 95m"
    assert node_terms("QUOTA_SHARE", {"cessionShare": 0.25, "eventCap": 40}) == "25% ceded · 40m event cap"
    assert node_terms("SURPLUS_SHARE", {"numLines": 4, "retentionLine": 25}) == "4 lines × 25m"
    assert node_terms("ILW_OPTION", {"limit": 25, "trigger": 40}) == "25m @ 40bn industry"
    assert node_terms(
        "CASCADING_TOWER",
        {"layers": [{"occLimit": 20, "occAttachment": 30}, {"occLimit": 30, "occAttachment": 50}]},
    ) == "2 layers · 50m xs 30m"
    assert node_terms("REGION_PERIL_FILTER", {"zones": ["US-FL", "US-TX"], "perils": ["WS"]}) == "US-FL/US-TX · WS"
    assert node_terms("REGION_PERIL_FILTER", {}) == "All zones & perils"
    assert node_terms("EVENT_ORDER_FILTER", {"minOrder": 2}) == "2nd+ events"
    assert node_terms("EVENT_ORDER_FILTER", {"minOrder": 2, "maxOrder": 3}) == "2nd–3rd events"
    assert node_terms("DATE_WINDOW_FILTER", {"startDay": 152, "endDay": 334}) == "Day 152–334"
    assert node_terms("LOSS_SIZE_FILTER", {"minLoss": 5}) == "≥ 5m"
    assert node_terms("UPLIFT", {"factor": 1.1, "additiveLoad": 2}) == "×1.10 + 2m"
    assert node_terms("LOSS_SOURCE", {"simSetId": "x"}) is None
    assert node_terms("YELT_OUTPUT", {"view": "UNADJUSTED"}) == "Unadjusted"
    assert node_terms("YELT_OUTPUT", {}) is None
    assert node_terms("PRICING_OUTPUT", {"contractId": "p1", "linkedLabel": "RL-2027-001 · 1st Cat XL"}) == "RL-2027-001 · 1st Cat XL"
    assert node_terms("PRICING_OUTPUT", {"linkedLabel": "stale"}) is None
    assert smart_label("PRICING_OUTPUT", {}, "Pricing Output — RL-2027-001 · 1st Cat XL") == "Pricing Output"
    assert smart_label("YELT_OUTPUT", {"view": "ADJUSTED"}, "YELT Output (Agg XL)") == "YELT Output — Adjusted"


def test_smart_label_keeps_name_and_respects_custom():
    params = {"occLimit": 60, "occAttachment": 80, "reinstatements": 1, "ripRate": 1.0}
    assert smart_label("OCC_XL", params, "1st Cat XL — 50m xs 80m (1@100%)") == "1st Cat XL — 60m xs 80m (1@100%)"
    assert smart_label("OCC_XL", {**params, "labelName": "Top Layer"}, "whatever") == "Top Layer — 60m xs 80m (1@100%)"
    assert smart_label("OCC_XL", {**params, "labelName": ""}, "old") == "60m xs 80m (1@100%)"
    assert smart_label("OCC_XL", {**params, "autoLabel": False}, "My bespoke label") == "My bespoke label"
    assert smart_label("YELT_OUTPUT", {}, "YELT Output (Agg XL)") == "YELT Output (Agg XL)"
    assert name_from_label("Zone Peril: US Wind (FL/TX/SE)") == "Zone Peril: US Wind"


def test_cms_layer_edit_relabels_node():
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": "Label Test Re",
            "programmeName": "Label Test XL",
            "treatyYear": 2027,
            "renewalPeriod": "J27",
            "office": "BDA",
            "underwriter": "Lead Underwriter",
            "broker": "Broker A",
            "status": "QUOTED",
            "direction": "ASSUMED",
            "structure": "OCC_XL",
            "limit": 40.0,
            "attachment": 60.0,
            "depositPremium": 8.0,
            "rol": 0.2,
            "collateralRequired": 40.0,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
        },
    )
    assert res.status_code == 200, res.text
    pid = res.json()["programmeId"]
    layer = client.get(f"/api/contracts/{pid}/layers").json()[0]
    assert layer["label"].endswith(layer["terms"])

    updated = client.put(
        f"/api/contracts/{pid}/layers",
        json={"layers": [{"nodeId": layer["nodeId"], "label": "Florida Top", "limit": 55, "signedLine": 0.5}]},
    ).json()[0]
    assert updated["name"] == "Florida Top"
    assert updated["label"].startswith("Florida Top — 55m xs 60m")
    assert updated["label"].endswith("· 50% share")

    graph = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]["graph"]
    for node in graph["nodes"]:
        if node["nodeId"] == layer["nodeId"]:
            node["params"]["occLimit"] = 80
            node["params"]["placedShare"] = 0.25
    saved = client.put(f"/api/programmes/{pid}/graph", json=graph)
    assert saved.status_code == 200, saved.text
    relabelled = client.get(f"/api/contracts/{pid}/layers").json()[0]
    assert relabelled["label"].startswith("Florida Top — 80m xs 60m")
    assert relabelled["label"].endswith("· 25% share")
    contract = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]
    assert contract["limit"] == 80
    assert contract["signedLine"] == 0.25
