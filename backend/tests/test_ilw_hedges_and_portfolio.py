"""Verification suite for ILW-as-programme, Long vs Hedge direction, DAG auto-wiring, and Portfolio hedge comparison."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_seeded_ilw_and_retro_live_in_contracts_with_auto_wired_sources():
    """ILWs and UNL retro hedges live alongside regular programmes in CMS with auto-wired DAG sources."""
    contracts = client.get("/api/contracts").json()
    by_id = {c["programmeId"]: c for c in contracts}

    # Seeded bound ILW hedge
    ilw = by_id["prog-ilw-fl-wind-27"]
    assert ilw["direction"] == "EXTERNAL_RETRO"
    assert ilw["isHedge"] is True
    assert ilw["structure"] == "ILW"
    assert ilw["instrument"] == "INDUSTRY_INDEX"
    assert ilw["simSetId"] == "ind-synth-cat-v1-100k"
    ilw_src = next(n for n in ilw["graph"]["nodes"] if n["nodeType"] == "INDUSTRY_INDEX_SOURCE")
    assert ilw_src["params"]["simSetId"] == "ind-synth-cat-v1-100k"
    ilw_rps = {f["regionPeril"] for f in ilw_src["params"]["lossFiles"]}
    assert {"US-FL-WS", "US-TX-WS", "US-SE-WS", "US-CA-EQ", "JP-KANTO-EQ", "EU-DE-WS"} <= ilw_rps
    assert any(n["nodeType"] == "ILW_OPTION" for n in ilw["graph"]["nodes"])

    # Seeded quoted UNL retro hedge
    retro = by_id["prog-retro-unl-27"]
    assert retro["direction"] == "EXTERNAL_RETRO"
    assert retro["isHedge"] is True
    assert retro["structure"] == "OCC_XL"
    assert retro["instrument"] == "UNL"
    assert retro["portfolioSnapshotDate"] == "2027-01-01"
    retro_src = next(n for n in retro["graph"]["nodes"] if n["nodeType"] == "PORTFOLIO_ROLLUP")
    assert retro_src["params"]["asOfDate"] == "2027-01-01"
    assert retro_src["params"]["sourceKind"] == "UNL_PORTFOLIO_SNAPSHOT"
    assert len(retro_src["params"]["lossFiles"]) >= 4


def test_ilw_programme_auto_wires_industry_source_and_allows_any_industry_filter():
    """Creating an ILW deal auto-wires to Industry Loss Run so all industry perils/regions are filterable."""
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": "Bermuda ILW Desk",
            "programmeName": "JP EQ $30B Binary ILW",
            "treatyYear": 2027,
            "renewalPeriod": "J27",
            "office": "BDA",
            "underwriter": "Lead Underwriter",
            "broker": "Broker C",
            "status": "QUOTED",
            "isHedge": True,
            "structure": "ILW",
            "instrument": "INDUSTRY_INDEX",
            "limit": 20.0,
            "attachment": 30.0,
            "depositPremium": 3.0,
            "rol": 0.15,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
        },
    )
    assert res.status_code == 200, res.text
    created = res.json()
    pid = created["programmeId"]
    assert created["direction"] == "EXTERNAL_RETRO"
    assert created["isHedge"] is True
    assert created["simSetId"] == "ind-synth-cat-v1-100k"
    assert created["collateralRequired"] == 0.0

    graph = created["graph"]
    src = next(n for n in graph["nodes"] if n["nodeType"] == "INDUSTRY_INDEX_SOURCE")
    flt = next(n for n in graph["nodes"] if n["nodeType"] == "REGION_PERIL_FILTER")
    lyr = next(n for n in graph["nodes"] if n["nodeType"] == "ILW_OPTION")
    px = next(n for n in graph["nodes"] if n["nodeType"] == "PRICING_OUTPUT")
    assert any(e["source"] == src["nodeId"] and e["target"] == flt["nodeId"] for e in graph["edges"])
    assert any(e["source"] == flt["nodeId"] and e["target"] == lyr["nodeId"] for e in graph["edges"])
    assert px["params"]["contractId"] == pid
    assert px["params"]["layerNodeId"] == lyr["nodeId"]

    # Switch the filter to a completely different industry region-peril (JP-KANTO-EQ) — must save and evaluate cleanly
    flt["params"]["regionPerils"] = ["JP-KANTO-EQ"]
    upd = client.put(f"/api/programmes/{pid}/graph", json=graph)
    assert upd.status_code == 200, upd.text

    ev = client.post(f"/api/programmes/{pid}/evaluate")
    assert ev.status_code == 200, ev.text
    adj = {m["nodeId"]: m for m in ev.json()["views"]["ADJUSTED"]}
    assert adj[src["nodeId"]]["el"] > 0.0
    assert adj[lyr["nodeId"]]["el"] > 0.0
    assert adj[px["nodeId"]]["pricing"]["isHedge"] is True
    assert adj[px["nodeId"]]["pricing"]["direction"] == "EXTERNAL_RETRO"


def test_unl_retro_hedge_auto_wires_to_portfolio_snapshot_and_respects_as_of_date():
    """Normal UNL retro hedge auto-wires to our UNL portfolio snapshot at asOfDate and filters/prices cleanly."""
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": "RoaringLion Own Book",
            "programmeName": "US Wind UNL Retro 30m xs 40m",
            "treatyYear": 2027,
            "renewalPeriod": "J27",
            "office": "BDA",
            "underwriter": "Lead Underwriter",
            "broker": "Broker A",
            "status": "QUOTED",
            "direction": "EXTERNAL_RETRO",
            "structure": "OCC_XL",
            "instrument": "UNL",
            "portfolioSnapshotDate": "2027-01-01",
            "limit": 30.0,
            "attachment": 40.0,
            "depositPremium": 3.6,
            "rol": 0.12,
            "regionPerils": ["US-FL-WS", "US-TX-WS", "US-SE-WS"],
            "perils": ["WS"],
        },
    )
    assert res.status_code == 200, res.text
    created = res.json()
    pid = created["programmeId"]
    assert created["isHedge"] is True
    assert created["portfolioSnapshotDate"] == "2027-01-01"

    graph = created["graph"]
    src = next(n for n in graph["nodes"] if n["nodeType"] == "PORTFOLIO_ROLLUP")
    flt = next(n for n in graph["nodes"] if n["nodeType"] == "REGION_PERIL_FILTER")
    lyr = next(n for n in graph["nodes"] if n["nodeType"] == "OCC_XL")
    assert src["params"]["asOfDate"] == "2027-01-01"
    assert any(e["source"] == src["nodeId"] and e["target"] == flt["nodeId"] for e in graph["edges"])

    # Source EL should match the gross bound UNL portfolio EL (excluding hedges and index/ILW deals)
    gross = client.get("/api/portfolio/rollup?view=ADJUSTED&totalCapital=160&includeHedges=false").json()
    ilw_ids = {c["programmeId"] for c in client.get("/api/contracts").json() if c["structure"] == "ILW"}
    gross_unl_el = sum(r["el"] for r in gross["layerAllocations"] if r["programmeId"] not in ilw_ids)
    ev_2027 = client.post(f"/api/programmes/{pid}/evaluate").json()
    adj_2027 = {m["nodeId"]: m for m in ev_2027["views"]["ADJUSTED"]}
    assert adj_2027[src["nodeId"]]["el"] == pytest.approx(gross_unl_el, abs=5e-3)
    assert adj_2027[lyr["nodeId"]]["el"] > 0.0

    # Snapshot info endpoint reports bound 2027 assumed UNL programmes vs historical 2026 snapshot
    snap_27 = client.get("/api/portfolio/snapshot-info?asOfDate=2027-01-01").json()
    snap_26 = client.get("/api/portfolio/snapshot-info?asOfDate=2026-06-15").json()
    assert snap_27["programmeCount"] >= 3
    assert snap_26["programmeCount"] >= 1
    assert "prog-farmers-us-26" in {p["programmeId"] for p in snap_26["programmes"]}

    # Changing the retro hedge's snapshot date to 2026-06-15 changes the UNL portfolio source stream
    upd = client.put(f"/api/contracts/{pid}", json={"portfolioSnapshotDate": "2026-06-15"})
    assert upd.status_code == 200, upd.text
    upd_src = next(n for n in upd.json()["graph"]["nodes"] if n["nodeType"] == "PORTFOLIO_ROLLUP")
    assert upd_src["params"]["asOfDate"] == "2026-06-15"
    ev_2026 = client.post(f"/api/programmes/{pid}/evaluate").json()
    adj_2026 = {m["nodeId"]: m for m in ev_2026["views"]["ADJUSTED"]}
    assert adj_2026[src["nodeId"]]["el"] < adj_2027[src["nodeId"]]["el"]


def test_direction_update_auto_rewires_source_and_persists():
    """Flipping a contract between Long UNL, Hedge UNL, and ILW persists direction and re-wires the DAG source."""
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": "Rewire Test Co",
            "programmeName": "Rewire Cat XL",
            "status": "QUOTED",
            "direction": "ASSUMED",
            "structure": "OCC_XL",
            "limit": 25.0,
            "attachment": 50.0,
            "depositPremium": 4.0,
            "rol": 0.16,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
        },
    )
    assert res.status_code == 200, res.text
    pid = res.json()["programmeId"]
    assert any(n["nodeType"] == "LOSS_SOURCE" for n in res.json()["graph"]["nodes"])
    assert res.json()["isHedge"] is False

    # Flip to Hedge (UNL retro) -> auto-rewires source to PORTFOLIO_ROLLUP
    as_hedge = client.put(f"/api/contracts/{pid}", json={"isHedge": True, "portfolioSnapshotDate": "2027-01-01"}).json()
    assert as_hedge["isHedge"] is True
    assert as_hedge["direction"] == "EXTERNAL_RETRO"
    assert any(n["nodeType"] == "PORTFOLIO_ROLLUP" for n in as_hedge["graph"]["nodes"])

    # Flip to ILW -> auto-rewires source to INDUSTRY_INDEX_SOURCE
    as_ilw = client.put(f"/api/contracts/{pid}", json={"structure": "ILW"}).json()
    assert as_ilw["structure"] == "ILW"
    assert as_ilw["instrument"] == "INDUSTRY_INDEX"
    assert any(n["nodeType"] == "INDUSTRY_INDEX_SOURCE" for n in as_ilw["graph"]["nodes"])

    # Flip back to Long UNL -> auto-rewires source to LOSS_SOURCE
    as_long = client.put(
        f"/api/contracts/{pid}",
        json={"isHedge": False, "structure": "OCC_XL", "instrument": "UNL", "simSetId": "sim-synth-cat-v1-100k"},
    ).json()
    assert as_long["isHedge"] is False
    assert as_long["direction"] == "ASSUMED"
    assert any(n["nodeType"] == "LOSS_SOURCE" for n in as_long["graph"]["nodes"])


def test_portfolio_comparison_with_vs_without_hedges_and_rollup_sign():
    """Portfolio comparison cleanly separates gross/long-only vs net-of-hedges, preserves Euler Co-TVaR, and scores accretion."""
    comp = client.get("/api/portfolio/comparison?view=ADJUSTED&totalCapital=160").json()
    without_h = comp["withoutHedges"]
    with_h = comp["withHedges"]
    acc = comp["accretion"]

    # Euler conservation on both rollups
    assert without_h["cotvarSumCheckDiff"] < 1e-9
    assert with_h["cotvarSumCheckDiff"] < 1e-9

    # Without hedges has only long layers; with hedges includes the bound ILW hedge with negative Co-TVaR/EL/premium
    assert without_h["includeHedges"] is False
    assert without_h["hedgeCount"] == 0
    assert all(not r["isHedge"] for r in without_h["layerAllocations"])

    assert with_h["includeHedges"] is True
    assert with_h["hedgeCount"] >= 1
    hedge_rows = [r for r in with_h["layerAllocations"] if r["isHedge"]]
    assert len(hedge_rows) == with_h["hedgeCount"]
    for hr in hedge_rows:
        assert hr["premium"] < 0
        assert hr["el"] < 0
        assert hr["eulerCotvar250"] < 0

    # Hedge reduces 1-in-250 TVaR and seeded ILW is accretive on Return on TVaR
    assert with_h["portfolioTvar250"] < without_h["portfolioTvar250"]
    assert acc["hasHedges"] is True
    assert acc["tvar250ReductionM"] > 10.0
    assert acc["deltaTvar250M"] == pytest.approx(with_h["portfolioTvar250"] - without_h["portfolioTvar250"], abs=1e-3)
    assert acc["deltaReturnOnTvarPct"] > 0.0
    assert acc["isAccretive"] is True
    assert acc["accretionVerdict"] == "Accretive"

    # Sliced rollup also respects hedge sign (Long ILW adds TVaR, Hedge ILW reduces TVaR)
    long_ilw = client.post(
        "/api/contracts",
        json={
            "cedentName": "ILW Buyer Co",
            "programmeName": "Sold FL Wind $40B ILW (Long)",
            "status": "QUOTED",
            "direction": "ASSUMED",
            "structure": "ILW",
            "instrument": "INDUSTRY_INDEX",
            "limit": 25.0,
            "attachment": 40.0,
            "depositPremium": 3.5,
            "rol": 0.14,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
        },
    ).json()
    slice_base = client.post(
        "/api/portfolio/rollup/slice",
        json={"programmeIds": ["prog-farmers-us-27"], "view": "ADJUSTED", "totalCapital": 160.0},
    ).json()
    slice_with_long_ilw = client.post(
        "/api/portfolio/rollup/slice",
        json={"programmeIds": ["prog-farmers-us-27", long_ilw["programmeId"]], "view": "ADJUSTED", "totalCapital": 160.0},
    ).json()
    slice_with_hedge_ilw = client.post(
        "/api/portfolio/rollup/slice",
        json={"programmeIds": ["prog-farmers-us-27", "prog-ilw-fl-wind-27"], "view": "ADJUSTED", "totalCapital": 160.0},
    ).json()

    assert slice_with_long_ilw["portfolioTvar250"] > slice_base["portfolioTvar250"]
    assert slice_with_hedge_ilw["portfolioTvar250"] < slice_base["portfolioTvar250"]
    assert slice_with_long_ilw["cotvarSumCheckDiff"] < 1e-9
    assert slice_with_hedge_ilw["cotvarSumCheckDiff"] < 1e-9

    # Overpriced hedge (95% ROL) should score as Dilutive
    expensive_hedge = client.post(
        "/api/contracts",
        json={
            "cedentName": "Expensive Protection",
            "programmeName": "Overpriced FL ILW",
            "status": "BOUND",
            "direction": "EXTERNAL_RETRO",
            "structure": "ILW",
            "instrument": "INDUSTRY_INDEX",
            "limit": 25.0,
            "attachment": 40.0,
            "depositPremium": 23.75,
            "rol": 0.95,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
        },
    ).json()
    try:
        dilutive_comp = client.get("/api/portfolio/comparison?view=ADJUSTED&totalCapital=160").json()
        assert dilutive_comp["accretion"]["isAccretive"] is False
        assert dilutive_comp["accretion"]["accretionVerdict"] == "Dilutive"
    finally:
        # Leave seeded state clean for any subsequent test in the session
        client.put(f"/api/contracts/{expensive_hedge['programmeId']}", json={"status": "DECLINED"})


def test_bound_unl_retro_hedge_reduces_portfolio_tvar_with_exact_conservation():
    """Binding the UNL retro hedge adds its negative Co-TVaR relief to the portfolio rollup."""
    before = client.get("/api/portfolio/comparison?view=ADJUSTED&totalCapital=160").json()
    client.put("/api/contracts/prog-retro-unl-27", json={"status": "BOUND"})
    try:
        after = client.get("/api/portfolio/comparison?view=ADJUSTED&totalCapital=160").json()
        assert after["withHedges"]["hedgeCount"] == before["withHedges"]["hedgeCount"] + 1
        assert after["withHedges"]["cotvarSumCheckDiff"] < 1e-9
        assert after["withHedges"]["portfolioTvar250"] < before["withHedges"]["portfolioTvar250"]
        retro_row = next(r for r in after["withHedges"]["layerAllocations"] if r["programmeId"] == "prog-retro-unl-27")
        assert retro_row["isHedge"] is True
        assert retro_row["eulerCotvar250"] < 0
        assert retro_row["premium"] < 0
    finally:
        client.put("/api/contracts/prog-retro-unl-27", json={"status": "QUOTED"})


def test_ilw_trigger_types_never_double_count_past_limit_downstream():
    """ILW_OPTION -> PRICING_OUTPUT -> YELT_OUTPUT stays capped at limit * placedShare on multi-event trials."""
    for trig_type, trig_bn, exh_bn in [
        ("BINARY", 15.0, None),
        ("PRO_RATA", 10.0, 40.0),
        ("AGGREGATE", 20.0, 60.0),
        ("SECOND_EVENT", 10.0, None),
    ]:
        res = client.post(
            "/api/contracts",
            json={
                "cedentName": "Cap Check Desk",
                "programmeName": f"Cap Check {trig_type} ILW",
                "status": "QUOTED",
                "direction": "EXTERNAL_RETRO",
                "structure": "ILW",
                "instrument": "INDUSTRY_INDEX",
                "limit": 25.0,
                "attachment": trig_bn,
                "depositPremium": 4.0,
                "rol": 0.16,
                "regionPerils": [],
                "perils": ["WS", "EQ"],
                "ilwSpec": {
                    "triggerType": trig_type,
                    "triggerBn": trig_bn,
                    "exhaustionBn": exh_bn,
                    "limitM": 25.0,
                    "rol": 0.16,
                },
            },
        )
        assert res.status_code == 200, res.text
        pid = res.json()["programmeId"]
        ev = client.post(f"/api/programmes/{pid}/evaluate").json()
        adj = {m["nodeType"]: m for m in ev["views"]["ADJUSTED"]}
        ilw_m = adj["ILW_OPTION"]
        px_m = adj["PRICING_OUTPUT"]
        yelt_m = adj["YELT_OUTPUT"]
        assert ilw_m["maxYear"] <= 25.0 + 1e-6
        assert px_m["maxYear"] <= 25.0 + 1e-6
        assert yelt_m["maxYear"] <= 25.0 + 1e-6
        assert px_m["el"] == pytest.approx(ilw_m["el"], abs=1e-4)
        assert yelt_m["el"] == pytest.approx(ilw_m["el"], abs=1e-4)
        # Industry sim set lineage recorded on DAG eval manifest
        assert "industry_sim_set" in ev["manifest"]["inputHashes"]
        assert any(b["entityType"] == "INDUSTRY_SIM_SET" for b in ev["manifest"]["breadcrumb"])


def test_snapshot_empty_window_stale_loss_files_lineage_exports_and_currency_guard():
    """Verify empty historical snapshot, stale lossFiles refresh on graph save, lineage, exports, and currency guard."""
    # Empty historical snapshot date does not silently fall back to 2027 bound book
    empty_snap = client.get("/api/portfolio/snapshot-info?asOfDate=2020-01-01").json()
    assert empty_snap["programmeCount"] == 0
    assert empty_snap["regionPerils"] == []

    # UNL retro DAG manifest records PORTFOLIO_SNAPSHOT provenance
    ev_retro = client.post("/api/programmes/prog-retro-unl-27/evaluate").json()
    assert any(b["entityType"] == "PORTFOLIO_SNAPSHOT" for b in ev_retro["manifest"]["breadcrumb"])
    assert any(k.startswith("portfolio_snapshot:") for k in ev_retro["manifest"]["inputHashes"])

    # Changing asOfDate on PORTFOLIO_ROLLUP to 2026-06-15 refreshes lossFiles so EU-DE-WS (not in 2026) is rejected
    retro = client.post(
        "/api/contracts",
        json={
            "cedentName": "Snapshot Filter Check",
            "programmeName": "Snapshot Filter Retro",
            "status": "QUOTED",
            "direction": "EXTERNAL_RETRO",
            "structure": "OCC_XL",
            "portfolioSnapshotDate": "2027-01-01",
            "limit": 20.0,
            "attachment": 40.0,
            "depositPremium": 2.0,
            "rol": 0.10,
            "regionPerils": ["EU-DE-WS"],
            "perils": ["WS"],
        },
    ).json()
    graph = retro["graph"]
    for n in graph["nodes"]:
        if n["nodeType"] == "PORTFOLIO_ROLLUP":
            n["params"]["asOfDate"] = "2026-06-15"
    rejected = client.put(f"/api/programmes/{retro['programmeId']}/graph", json=graph)
    assert rejected.status_code == 422
    assert "EU-DE-WS" in rejected.json()["detail"]

    # Comparison response includes top-level manifest
    comp = client.get("/api/portfolio/comparison?view=ADJUSTED&totalCapital=160").json()
    assert comp["manifest"]["manifestId"].startswith("man-portfolio_hedge_comparison-")

    # Exports reflect includeHedges=true vs includeHedges=false and Direction column
    csv_with = client.get("/api/exports/portfolio-csv?view=ADJUSTED&totalCapital=160&includeHedges=true").text
    csv_without = client.get("/api/exports/portfolio-csv?view=ADJUSTED&totalCapital=160&includeHedges=false").text
    assert "Hedge" in csv_with
    assert "ILW Market Protection" in csv_with
    assert "ILW Market Protection" not in csv_without

    html_with = client.get("/api/exports/portfolio-tearsheet?view=ADJUSTED&totalCapital=160&includeHedges=true").text
    assert "Return on 1-in-250 TVaR" in html_with
    assert "Hedge" in html_with

    # Mixed-currency portfolio rollup is rejected cleanly with 422
    from app.db.database import get_session
    from app.db.models import ContractRecord
    from app.main import _sync_memory_aliases

    eur_deal = client.post(
        "/api/contracts",
        json={
            "cedentName": "Euro Currency Check",
            "programmeName": "EUR Cat XL",
            "status": "QUOTED",
            "office": "LON",
            "direction": "ASSUMED",
            "structure": "OCC_XL",
            "currency": "EUR",
            "limit": 20.0,
            "attachment": 40.0,
            "depositPremium": 3.0,
            "rol": 0.15,
            "regionPerils": ["EU-DE-WS"],
            "perils": ["WS"],
        },
    ).json()
    try:
        mixed = client.post(
            "/api/portfolio/rollup/slice",
            json={"programmeIds": ["prog-farmers-us-27", eur_deal["programmeId"]], "view": "ADJUSTED", "totalCapital": 160.0},
        )
        assert mixed.status_code == 422
        assert "currencies" in mixed.json()["detail"].lower()
    finally:
        with get_session() as db:
            rec = db.get(ContractRecord, eur_deal["programmeId"])
            if rec:
                db.delete(rec)
                db.commit()
        _sync_memory_aliases()
