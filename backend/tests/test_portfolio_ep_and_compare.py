"""Backend tests for live portfolio EP curves (OEP/AEP/TVaR), Region/Peril/Region-Peril splits,
and Portfolio-to-Portfolio timeline comparison (YoY, Plan vs Current, custom slices).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

EXPECTED_RPS = [10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0]


def test_portfolio_rollup_includes_live_ep_curve_and_region_peril_splits():
    resp = client.get("/api/portfolio/comparison?view=ADJUSTED&totalCapital=160")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    for side_key in ("withoutHedges", "withHedges"):
        rollup = body[side_key]
        ep_curve = rollup["epCurve"]
        assert [p["returnPeriod"] for p in ep_curve] == EXPECTED_RPS

        pt250 = next(p for p in ep_curve if p["returnPeriod"] == 250.0)
        assert abs(pt250["aep"] - rollup["portfolioVar250"]) < 1e-3
        assert abs(pt250["aepTvar"] - rollup["portfolioTvar250"]) < 1e-3

        # Monotonicity across return periods
        for prev, nxt in zip(ep_curve, ep_curve[1:]):
            assert nxt["oep"] >= prev["oep"] - 1e-6
            assert nxt["aep"] >= prev["aep"] - 1e-6
            assert nxt["aepTvar"] >= prev["aepTvar"] - 1e-6

        # On gross (long-only) book, OEP <= AEP and VaR <= TVaR at every return period
        if side_key == "withoutHedges":
            for pt in ep_curve:
                assert pt["oep"] <= pt["aep"] + 1e-6
                assert pt["oep"] <= pt["oepTvar"] + 1e-6
                assert pt["aep"] <= pt["aepTvar"] + 1e-6

        # Region, Peril, and Region-Peril splits
        regions = rollup["epByRegion"]
        perils = rollup["epByPeril"]
        rps = rollup["epByRegionPeril"]

        region_keys = {s["key"] for s in regions}
        peril_keys = {s["key"] for s in perils}
        assert {"US", "JP", "EU", "AU", "GL"}.issubset(region_keys)
        assert {"WS", "EQ", "CS", "WF", "CY"}.issubset(peril_keys)
        assert len(rps) == 13

        # Expected loss (AAL) additivity across every split dimension
        assert abs(sum(s["aal"] for s in regions) - rollup["expectedLoss"]) < 0.02
        assert abs(sum(s["aal"] for s in perils) - rollup["expectedLoss"]) < 0.02
        assert abs(sum(s["aal"] for s in rps) - rollup["expectedLoss"]) < 0.02

        # TVaR subadditivity (diversification benefit across regions)
        if side_key == "withoutHedges":
            region_tvar_250s = [
                next(p["aepTvar"] for p in s["epPoints"] if p["returnPeriod"] == 250.0)
                for s in regions
            ]
            assert rollup["portfolioTvar250"] <= sum(region_tvar_250s) + 0.05

    # Hedges reduce 1-in-250 AEP TVaR vs long-only
    gross_250 = next(p for p in body["withoutHedges"]["epCurve"] if p["returnPeriod"] == 250.0)
    net_250 = next(p for p in body["withHedges"]["epCurve"] if p["returnPeriod"] == 250.0)
    assert net_250["aepTvar"] < gross_250["aepTvar"]


def test_portfolio_compare_options_and_presets_match_seeded_book():
    resp = client.get("/api/portfolio/compare/options")
    assert resp.status_code == 200, resp.text
    opts = resp.json()

    assert 2027 in opts["treatyYears"]
    assert 2026 in opts["treatyYears"]
    assert len(opts["contracts"]) >= 48

    preset_ids = {p["id"] for p in opts["presets"]}
    assert {"plan-vs-current", "yoy-book", "yoy-renewals", "net-vs-gross"}.issubset(preset_ids)

    # Every preset must execute cleanly and return non-empty splits and exact deltas
    for preset in opts["presets"]:
        cr = client.post(
            "/api/portfolio/compare",
            json={"portfolioA": preset["portfolioA"], "portfolioB": preset["portfolioB"]},
        )
        assert cr.status_code == 200, f"Preset {preset['id']} failed: {cr.text}"
        data = cr.json()
        assert data["programmeCountA"] > 0
        assert data["programmeCountB"] > 0
        assert [pt["returnPeriod"] for pt in data["epDeltas"]] == EXPECTED_RPS
        assert len(data["regionDeltas"]) >= 1
        assert len(data["perilDeltas"]) >= 1
        assert len(data["regionPerilDeltas"]) >= 1

        # Verify delta arithmetic matches A - B
        pa = data["portfolioA"]
        pb = data["portfolioB"]
        d = data["delta"]
        assert abs(d["deltaNetPremiumM"] - (pa["netPremium"] - pb["netPremium"])) < 1e-3
        assert abs(d["deltaExpectedLossM"] - (pa["expectedLoss"] - pb["expectedLoss"])) < 1e-3
        assert abs(d["deltaExpectedProfitM"] - (pa["expectedProfit"] - pb["expectedProfit"])) < 1e-3
        assert abs(d["deltaTvar250M"] - (pa["portfolioTvar250"] - pb["portfolioTvar250"])) < 1e-3


def test_portfolio_compare_yoy_and_date_range_and_empty_slice_422():
    # Custom date-range comparison: 2027 vs 2026
    resp = client.post(
        "/api/portfolio/compare",
        json={
            "portfolioA": {
                "label": "2027 Bound",
                "inceptionFrom": "2027-01-01",
                "inceptionTo": "2027-12-31",
                "statuses": ["BOUND"],
                "includeHedges": True,
                "view": "ADJUSTED",
                "totalCapital": 160,
            },
            "portfolioB": {
                "label": "2026 Prior",
                "inceptionFrom": "2026-01-01",
                "inceptionTo": "2026-12-31",
                "statuses": ["EXPIRED"],
                "includeHedges": True,
                "view": "ADJUSTED",
                "totalCapital": 160,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["programmeCountA"] >= 25
    assert body["programmeCountB"] == 5
    assert body["delta"]["deltaNetPremiumM"] > 0
    assert any(r["key"] == "US" for r in body["regionDeltas"])

    # Empty slice (2026 has only EXPIRED contracts, no BOUND) -> 422 with clear detail
    bad = client.post(
        "/api/portfolio/compare",
        json={
            "portfolioA": {
                "label": "Empty 2026 Bound",
                "treatyYears": [2026],
                "statuses": ["BOUND"],
            },
            "portfolioB": {
                "label": "2027 Bound",
                "treatyYears": [2027],
                "statuses": ["BOUND"],
            },
        },
    )
    assert bad.status_code == 422
    assert "matched 0 contracts" in bad.json()["detail"]


def test_portfolio_csv_and_tearsheet_exports_contain_ep_return_periods():
    resp = client.get("/api/exports/portfolio-csv?view=ADJUSTED&totalCapital=160&includeHedges=true")
    assert resp.status_code == 200
    text = resp.text
    assert "Exceedance Probability Curve" in text
    assert "OEP ($M)" in text
    assert "AEP TVaR ($M)" in text

    html_resp = client.get("/api/exports/portfolio-tearsheet?view=ADJUSTED&totalCapital=160&includeHedges=true")
    assert html_resp.status_code == 200
    assert "Computed Return Period Losses" in html_resp.text
    assert "Risk Appetite Guidelines" not in html_resp.text
    assert "1-in-250" in html_resp.text


def test_portfolio_compare_rejects_cross_currency_without_fx():
    from app.engine.slice_runner import evaluate_portfolio_comparison
    from app.main import CONTRACT_STORE, SIM_STORE, _base_yelt
    from app.models.enums import ViewOfRisk
    from app.services.contracts import contract_to_programme_deal
    from app.services.lineage import LineageRegistry
    import pytest

    base_deal = contract_to_programme_deal(CONTRACT_STORE.get_contract("prog-farmers-us-27"))
    eur_deal = base_deal.model_copy(update={"programme_id": "prog-eur-test", "currency": "EUR"})
    yelt = SIM_STORE.get_yelt(_base_yelt.sim_set_id)

    with pytest.raises(ValueError, match="different currencies"):
        evaluate_portfolio_comparison(
            label_a="USD Book",
            programmes_a=[base_deal],
            view_a=ViewOfRisk.ADJUSTED,
            total_capital_a=160.0,
            include_hedges_a=True,
            label_b="EUR Book",
            programmes_b=[eur_deal],
            view_b=ViewOfRisk.ADJUSTED,
            total_capital_b=160.0,
            include_hedges_b=True,
            yelt=yelt,
            lineage=LineageRegistry(persist=False),
        )
