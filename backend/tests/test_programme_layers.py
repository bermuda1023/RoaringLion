"""Programmes with many layers: create with a tower, add and remove layers, totals roll up."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _create(name: str, layers: list[dict] | None = None) -> dict:
    payload = {
        "cedentName": f"{name} Re",
        "programmeName": f"{name} Tower",
        "status": "QUOTED",
        "direction": "ASSUMED",
        "limit": 40.0,
        "attachment": 60.0,
        "depositPremium": 8.0,
        "regionPerils": ["US-FL-WS", "US-TX-WS"],
        "perils": ["WS"],
    }
    if layers is not None:
        payload["layers"] = layers
    res = client.post("/api/contracts", json=payload)
    assert res.status_code == 200, res.text
    return res.json()


def _adj(pid: str) -> dict[str, dict]:
    res = client.post(f"/api/programmes/{pid}/evaluate")
    assert res.status_code == 200, res.text
    return {m["nodeId"]: m for m in res.json()["views"]["ADJUSTED"]}


def test_create_programme_with_many_layers():
    c = _create(
        "Many",
        [
            {"limit": 25, "attachment": 25, "depositPremium": 7.5, "signedLine": 0.5, "writtenLine": 0.6},
            {"name": "Middle", "limit": 50, "attachment": 50, "depositPremium": 8.0, "signedLine": 0.25},
            {"layerType": "AGG_XL", "limit": 30, "attachment": 60, "depositPremium": 3.0, "perEventCap": 20},
        ],
    )
    pid = c["programmeId"]
    layers = client.get(f"/api/contracts/{pid}/layers").json()
    assert [l["name"] for l in layers] == ["1st Cat XL", "Middle", "3rd Agg XL"]
    assert [l["nodeType"] for l in layers] == ["OCC_XL", "OCC_XL", "AGG_XL"]
    assert layers[0]["label"].startswith("1st Cat XL — 25m xs 25m")
    assert layers[2]["terms"] == "30m xs 60m · 20m event cap"

    assert c["limit"] == 105
    assert c["depositPremium"] == 18.5
    assert c["attachment"] == 25
    assert abs(c["signedLine"] - (25 * 0.5 + 50 * 0.25 + 30 * 1.0) / 105) < 1e-6
    assert abs(c["writtenLine"] - (25 * 0.6 + 50 * 0.25 + 30 * 1.0) / 105) < 1e-6

    nodes = {n["nodeId"]: n for n in c["graph"]["nodes"]}
    pricing = [n for n in nodes.values() if n["nodeType"] == "PRICING_OUTPUT"]
    assert len(pricing) == 3
    assert sorted(n["label"] for n in pricing) == ["Pricing · 1st Cat XL", "Pricing · 3rd Agg XL", "Pricing · Middle"]
    yelt_inputs = {e["source"] for e in c["graph"]["edges"] if e["target"] == f"out-{pid}"}
    assert yelt_inputs == {l["nodeId"] for l in layers}

    metrics = _adj(pid)
    for layer in layers:
        priced = metrics[f"px-{layer['nodeId']}"]["pricing"]
        assert priced["layerNodeId"] == layer["nodeId"]
        assert abs(priced["expectedLoss"] - metrics[layer["nodeId"]]["el"]) < 1e-3
    total_el = sum(metrics[l["nodeId"]]["el"] for l in layers)
    assert abs(metrics[f"out-{pid}"]["el"] - total_el) < 1e-3


def test_single_layer_programme_still_defaults_from_limit():
    c = _create("Single")
    layers = client.get(f"/api/contracts/{c['programmeId']}/layers").json()
    assert len(layers) == 1
    assert layers[0]["limit"] == 40 and layers[0]["attachment"] == 60
    assert layers[0]["name"] == "Single Tower"


def test_add_and_remove_layers():
    c = _create("Grow")
    pid = c["programmeId"]
    first = client.get(f"/api/contracts/{pid}/layers").json()[0]

    added = client.post(
        f"/api/contracts/{pid}/layers",
        json={"name": "Top", "limit": 60, "attachment": 100, "depositPremium": 6.0, "signedLine": 0.5},
    )
    assert added.status_code == 200, added.text
    layers = added.json()
    assert [l["name"] for l in layers] == ["Grow Tower", "Top"]
    top = layers[1]
    assert top["writtenLine"] == 0.5 and top["signedLine"] == 0.5

    contract = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]
    assert contract["limit"] == 100
    assert contract["depositPremium"] == 14.0
    graph = contract["graph"]
    inputs = {e["source"] for e in graph["edges"] if e["target"] == top["nodeId"]}
    assert inputs == {f"flt-{pid}"}
    assert {e["source"] for e in graph["edges"] if e["target"] == f"out-{pid}"} == {first["nodeId"], top["nodeId"]}
    assert _adj(pid)[f"px-{top['nodeId']}"]["pricing"]["ourPremium"] == 3.0

    third = client.post(f"/api/contracts/{pid}/layers", json={"layerType": "AGG_XL", "limit": 20, "attachment": 40}).json()[2]
    assert third["name"] == "3rd Agg XL"

    removed = client.delete(f"/api/contracts/{pid}/layers/{top['nodeId']}")
    assert removed.status_code == 200, removed.text
    assert [l["nodeId"] for l in removed.json()] == [first["nodeId"], third["nodeId"]]
    graph = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]["graph"]
    ids = {n["nodeId"] for n in graph["nodes"]}
    assert f"px-{top['nodeId']}" not in ids
    assert f"out-{pid}" in ids

    client.delete(f"/api/contracts/{pid}/layers/{third['nodeId']}")
    last = client.delete(f"/api/contracts/{pid}/layers/{first['nodeId']}")
    assert last.status_code == 422
    assert "at least one layer" in last.json()["detail"]
    assert client.delete(f"/api/contracts/{pid}/layers/nope").status_code == 404
    bad = client.post(f"/api/contracts/{pid}/layers", json={"limit": 0, "attachment": 10})
    assert bad.status_code == 422


def test_each_layer_has_its_own_price_lines_rarc_brokerage_and_loss_ratio():
    c = _create(
        "Priced",
        [
            {"limit": 20, "attachment": 30, "rol": 0.25, "writtenLine": 0.4,
             "signedLine": 0.3, "brokeragePct": 0.15, "rarcPct": 10.0},
            {"limit": 40, "attachment": 50, "rol": 0.10, "writtenLine": 0.2,
             "signedLine": 0.1, "brokeragePct": 0.05, "rarcPct": 4.0},
        ],
    )
    pid = c["programmeId"]
    low, high = client.get(f"/api/contracts/{pid}/layers").json()
    assert low["depositPremium"] == 5.0 and abs(low["rol"] - 0.25) < 1e-9
    assert (low["writtenLine"], low["signedLine"]) == (0.4, 0.3)
    assert "authorizedLine" not in low and "technicalRol" not in low and "priceAdequacy" not in low
    assert "authorizedLine" not in low["editable"] and "technicalRol" not in low["editable"]
    assert {"rol", "brokeragePct", "rarcPct", "writtenLine", "signedLine"} <= set(low["editable"])
    assert low["brokeragePct"] == 0.15 and low["rarcPct"] == 10.0
    assert abs(low["ourNetPremium"] - 5.0 * 0.3 * 0.85) < 1e-6
    assert high["depositPremium"] == 4.0 and high["rarcPct"] == 4.0

    # Loss ratio is read-only and derived from modelled Adjusted EL / (Our Deposit Premium + Expected RIP)
    assert low["expectedLoss"] is not None and low["lossRatio"] is not None
    expected_low_lr = low["expectedLoss"] / (low["ourPremium"] + (low["expectedRip"] or 0.0))
    assert abs(low["lossRatio"] - expected_low_lr) < 1e-5
    assert high["expectedLoss"] is not None and high["lossRatio"] is not None

    # Contract totals roll up; RARC is signed-line-size weighted: (20*0.3*10 + 40*0.1*4) / (6 + 4) = 7.6%
    assert "authorizedLine" not in c and "technicalRol" not in c
    assert c["depositPremium"] == 9.0
    assert abs(c["brokeragePct"] - (5.0 * 0.15 + 4.0 * 0.05) / 9.0) < 1e-6
    assert abs(c["writtenLine"] - (20 * 0.4 + 40 * 0.2) / 60) < 1e-6
    assert abs(c["profile"]["rarcTotalPct"] - 7.6) < 1e-4

    priced = _adj(pid)[f"px-{low['nodeId']}"]["pricing"]
    assert priced["brokeragePct"] == 0.15
    assert "technicalRol" not in priced and "priceAdequacy" not in priced
    assert abs(priced["lossRatio"] - low["lossRatio"]) < 1e-5

    # Updating layer ROL, brokerage, and RARC updates loss ratio and recomputes weighted programme RARC
    prev_high_lr = high["lossRatio"]
    res = client.put(
        f"/api/contracts/{pid}/layers",
        json={"layers": [{"nodeId": high["nodeId"], "rol": 0.20, "rarcPct": -2.0, "brokeragePct": 0.2}]},
    )
    assert res.status_code == 200, res.text
    high = res.json()[1]
    assert high["depositPremium"] == 8.0
    assert high["rarcPct"] == -2.0
    # Doubling deposit premium lowers loss ratio
    assert high["lossRatio"] < prev_high_lr
    dossier = client.get(f"/api/contracts/{pid}/dossier").json()
    contract = dossier["contract"]
    assert contract["depositPremium"] == 13.0
    # (6 * 10.0 + 4 * -2.0) / 10 = 5.2%
    assert abs(contract["profile"]["rarcTotalPct"] - 5.2) < 1e-4
    assert dossier["pricingLink"]["programmeLossRatio"] is not None

    # Programme RARC cannot be manually overwritten when layers define RARC
    upd = client.put(
        f"/api/contracts/{pid}",
        json={"profile": {**contract["profile"], "rarcTotalPct": 42.0}},
    )
    assert upd.status_code == 200
    assert abs(upd.json()["profile"]["rarcTotalPct"] - 5.2) < 1e-4

    # Clearing RARC on all layers resets programme RARC to 0, and manual profile PUT still cannot overwrite it
    cleared = client.put(
        f"/api/contracts/{pid}/layers",
        json={"layers": [{"nodeId": low["nodeId"], "rarcPct": None}, {"nodeId": high["nodeId"], "rarcPct": None}]},
    )
    assert cleared.status_code == 200
    assert all(l["rarcPct"] is None for l in cleared.json())
    assert client.get(f"/api/contracts/{pid}/dossier").json()["contract"]["profile"]["rarcTotalPct"] == 0.0

    manual_after_clear = client.put(
        f"/api/contracts/{pid}",
        json={"profile": {**contract["profile"], "rarcTotalPct": 19.5}},
    )
    assert manual_after_clear.status_code == 200
    assert manual_after_clear.json()["profile"]["rarcTotalPct"] == 0.0

    # Deleting the only RARC-bearing layer recalculates/resets programme RARC to 0
    set_one = client.put(
        f"/api/contracts/{pid}/layers",
        json={"layers": [{"nodeId": high["nodeId"], "rarcPct": 8.5}]},
    )
    assert set_one.status_code == 200
    assert abs(client.get(f"/api/contracts/{pid}/dossier").json()["contract"]["profile"]["rarcTotalPct"] - 8.5) < 1e-4
    del_res = client.delete(f"/api/contracts/{pid}/layers/{high['nodeId']}")
    assert del_res.status_code == 200
    assert client.get(f"/api/contracts/{pid}/dossier").json()["contract"]["profile"]["rarcTotalPct"] == 0.0

    bad_br = client.put(f"/api/contracts/{pid}/layers", json={"layers": [{"nodeId": low["nodeId"], "brokeragePct": 0.95}]})
    assert bad_br.status_code == 422
    bad_rarc = client.put(f"/api/contracts/{pid}/layers", json={"layers": [{"nodeId": low["nodeId"], "rarcPct": -150.0}]})
    assert bad_rarc.status_code == 422


def test_rarc_weighting_falls_back_to_written_line_then_limit_when_signed_is_zero():
    c = _create(
        "QuotedRarc",
        [
            {"limit": 20, "attachment": 30, "rol": 0.20, "writtenLine": 0.5, "signedLine": 0.0, "rarcPct": 12.0},
            {"limit": 60, "attachment": 50, "rol": 0.10, "writtenLine": 0.5, "signedLine": 0.0, "rarcPct": 4.0},
        ],
    )
    pid = c["programmeId"]
    # Signed line size is 0 on all layers -> weights by written line size: (20*0.5*12 + 60*0.5*4) / (10 + 30) = 6.0%
    assert abs(c["profile"]["rarcTotalPct"] - 6.0) < 1e-4

    # Both signed and written 0 -> weights by 100% limit: (20*12 + 60*4) / 80 = 6.0%;
    # change layer 2 RARC to 0% -> (20*12 + 60*0) / 80 = 3.0%
    layers = client.get(f"/api/contracts/{pid}/layers").json()
    res = client.put(
        f"/api/contracts/{pid}/layers",
        json={
            "layers": [
                {"nodeId": layers[0]["nodeId"], "writtenLine": 0.0, "rarcPct": 12.0},
                {"nodeId": layers[1]["nodeId"], "writtenLine": 0.0, "rarcPct": 0.0},
            ]
        },
    )
    assert res.status_code == 200
    updated = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]
    assert abs(updated["profile"]["rarcTotalPct"] - 3.0) < 1e-4


def test_legacy_authorized_line_falls_back_to_written_on_read_and_db_migration(tmp_path):
    from sqlalchemy import create_engine, text
    from app.db.database import init_engine
    from app.models.domain import GraphNodeSpec, GraphSpec
    from app.models.enums import NodeType
    from app.services.contracts import apply_layer_updates, graph_layers
    from app.models.phase1 import ContractLayerUpdate

    # 1. Legacy graph node with authorizedLine but no writtenLine reads writtenLine from authorizedLine
    legacy_graph = GraphSpec(
        graph_id="grp-legacy",
        name="Legacy",
        nodes=[
            GraphNodeSpec(
                node_id="lyr-legacy",
                node_type=NodeType.OCC_XL,
                label="Legacy Layer",
                params={
                    "layerCode": "L1",
                    "occLimit": 30.0,
                    "occAttachment": 50.0,
                    "depositPremium": 6.0,
                    "placedShare": 0.2,
                    "authorizedLine": 0.35,
                    "technicalRol": 0.18,
                },
            )
        ],
        edges=[],
    )
    layers = graph_layers(legacy_graph, default_written=1.0, default_signed=0.2)
    assert len(layers) == 1
    assert layers[0].written_line == 0.35
    assert layers[0].signed_line == 0.2

    # Updating the layer cleans up legacy authorizedLine and technicalRol params
    apply_layer_updates(legacy_graph, [ContractLayerUpdate(node_id="lyr-legacy", rarc_pct=5.5)])
    assert "authorizedLine" not in legacy_graph.nodes[0].params
    assert "technicalRol" not in legacy_graph.nodes[0].params
    assert legacy_graph.nodes[0].params["rarcPct"] == 5.5

    # 2. Full pre-change SQLite DB with authorized_line NOT NULL + technical_rol migrates cleanly
    #    and supports inserting new contracts afterwards.
    from app.models.phase1 import ContractCreate
    from app.services.contracts import ContractStore
    from app.services.lineage import LineageRegistry

    db_file = tmp_path / "legacy.db"
    url = f"sqlite:///{db_file}"
    raw_eng = create_engine(url)
    with raw_eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE contracts ("
                "programme_id VARCHAR(80) PRIMARY KEY, "
                "chain_id VARCHAR(80) NOT NULL DEFAULT '', "
                "prior_contract_id VARCHAR(80), "
                "cedent_name VARCHAR(160) NOT NULL DEFAULT 'Old Re', "
                "programme_name VARCHAR(200) NOT NULL DEFAULT 'Old Tower', "
                "treaty_year INTEGER NOT NULL DEFAULT 2026, "
                "renewal_period VARCHAR(20) NOT NULL DEFAULT 'J26', "
                "office VARCHAR(20) NOT NULL DEFAULT 'BDA', "
                "underwriter VARCHAR(100) NOT NULL DEFAULT 'UW', "
                "secondary_underwriter VARCHAR(100), "
                "broker VARCHAR(120) NOT NULL DEFAULT 'Direct', "
                "fronting_carrier VARCHAR(120), "
                "collateral_cell VARCHAR(120), "
                "status VARCHAR(30) NOT NULL DEFAULT 'QUOTED', "
                "direction VARCHAR(30) NOT NULL DEFAULT 'ASSUMED', "
                "structure VARCHAR(30) NOT NULL DEFAULT 'OCC_XL', "
                "instrument VARCHAR(30) NOT NULL DEFAULT 'UNL', "
                "placement VARCHAR(30) NOT NULL DEFAULT 'OPEN_MARKET', "
                "inception_date VARCHAR(30), "
                "expiry_date VARCHAR(30), "
                "hours_clause INTEGER NOT NULL DEFAULT 72, "
                "authorized_line FLOAT NOT NULL, "
                "written_line FLOAT, "
                "signed_line FLOAT NOT NULL DEFAULT 1.0, "
                "\"limit\" FLOAT NOT NULL DEFAULT 25.0, "
                "attachment FLOAT NOT NULL DEFAULT 50.0, "
                "deposit_premium FLOAT NOT NULL DEFAULT 4.0, "
                "rol FLOAT NOT NULL DEFAULT 0.16, "
                "technical_rol FLOAT, "
                "brokerage_pct FLOAT NOT NULL DEFAULT 0.1, "
                "fronting_fee_pct FLOAT NOT NULL DEFAULT 0.02, "
                "collateral_required FLOAT NOT NULL DEFAULT 25.0, "
                "currency VARCHAR(8) NOT NULL DEFAULT 'USD', "
                "sim_set_id VARCHAR(80) NOT NULL DEFAULT 'sim-synth-cat-v1-100k', "
                "exposure_eclipse_programme_id VARCHAR(80), "
                "region_perils_json TEXT NOT NULL DEFAULT '[\"US-FL-WS\"]', "
                "perils_json TEXT NOT NULL DEFAULT '[\"WS\"]', "
                "custom_tags_json TEXT NOT NULL DEFAULT '[]', "
                "uplift_set_json TEXT NOT NULL DEFAULT '{}', "
                "graph_json TEXT NOT NULL DEFAULT '{}', "
                "ilw_spec_json TEXT, "
                "profile_json TEXT NOT NULL DEFAULT '{}', "
                "version INTEGER NOT NULL DEFAULT 1, "
                "content_hash VARCHAR(32) NOT NULL DEFAULT '', "
                "updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO contracts (programme_id, authorized_line, written_line, technical_rol) "
                "VALUES ('p-legacy', 0.45, NULL, 0.14), ('p-modern', 0.90, 0.30, 0.15)"
            )
        )
    raw_eng.dispose()

    # Run migration against legacy DB, verify written_line backfilled, legacy cols dropped, and new insert works
    import app.db.database as db_mod
    prev_engine, prev_session = db_mod._engine, db_mod._SessionLocal
    try:
        migrated_eng = init_engine(url)
        with migrated_eng.connect() as conn:
            rows = dict(conn.execute(text("SELECT programme_id, written_line FROM contracts")).all())
            col_names = {r[1] for r in conn.execute(text("PRAGMA table_info(contracts)")).all()}
        assert rows["p-legacy"] == 0.45
        assert rows["p-modern"] == 0.30
        assert "authorized_line" not in col_names
        assert "technical_rol" not in col_names

        # Inserting a brand new contract on the migrated DB must succeed without NOT NULL errors
        store = ContractStore()
        created = store.create_contract(
            ContractCreate(
                cedent_name="Post-Migration Re",
                programme_name="Migrated Tower",
                written_line=0.5,
                signed_line=0.4,
            ),
            LineageRegistry(persist=False),
        )
        assert created.written_line == 0.5 and created.signed_line == 0.4
        migrated_eng.dispose()
    finally:
        db_mod._engine, db_mod._SessionLocal = prev_engine, prev_session


def test_unique_layer_identifiers_and_structuring_suggestions():
    # Creating a multi-layer programme assigns unique layer codes within the programme
    c = _create(
        "Suggest",
        [
            {"name": "Primary", "limit": 25, "attachment": 25, "depositPremium": 6.0},
            {"layerCode": "CAT-2", "name": "Second", "limit": 50, "attachment": 50, "depositPremium": 8.0},
        ],
    )
    pid = c["programmeId"]
    layers = client.get(f"/api/contracts/{pid}/layers").json()
    assert [l["layerCode"] for l in layers] == ["L1", "CAT-2"]

    # Another programme can reuse the same layer codes independently
    other = _create("Other", [{"layerCode": "CAT-2", "limit": 20, "attachment": 40, "depositPremium": 3.0}])
    other_layers = client.get(f"/api/contracts/{other['programmeId']}/layers").json()
    assert [l["layerCode"] for l in other_layers] == ["CAT-2"]

    # Duplicate layerCode on create_contract is rejected
    dup_create = client.post(
        "/api/contracts",
        json={
            "cedentName": "Dup Re",
            "programmeName": "Dup Tower",
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
            "layers": [
                {"layerCode": "L1", "limit": 20, "attachment": 20},
                {"layerCode": "l1", "limit": 30, "attachment": 40},
            ],
        },
    )
    assert dup_create.status_code == 422

    # Adding a new layer auto-assigns a unique layerCode and shows up in Structuring Studio suggestions
    added = client.post(
        f"/api/contracts/{pid}/layers",
        json={"name": "Top Agg", "layerType": "AGG_XL", "limit": 40, "attachment": 100, "depositPremium": 5.0},
    )
    assert added.status_code == 200, added.text
    layers = added.json()
    assert [l["layerCode"] for l in layers] == ["L1", "CAT-2", "L2"]
    new_layer = layers[2]

    # Duplicate layerCode on POST /layers is rejected (case-insensitive)
    dup_add = client.post(
        f"/api/contracts/{pid}/layers",
        json={"layerCode": "cat-2", "name": "Collision", "limit": 10, "attachment": 150},
    )
    assert dup_add.status_code == 422

    # Structuring Studio suggestions include every layer on the programme, including the newly created one
    tpls = client.get(f"/api/graph-templates?programmeId={pid}").json()
    cms_layers = [t for t in tpls if t.get("source") == "CMS" and t["templateId"].startswith(f"cms-layer-{pid}-")]
    assert [t["layerCode"] for t in cms_layers] == ["L1", "CAT-2", "L2"]
    top_tpl = next(t for t in cms_layers if t["layerCode"] == new_layer["layerCode"])
    assert top_tpl["nodeType"] == "AGG_XL"
    assert top_tpl["label"] == new_layer["label"]
    assert top_tpl["params"]["layerCode"] == "L2"
    assert top_tpl["params"]["aggLimit"] == 40
    assert top_tpl["params"]["aggAttachment"] == 100

    # Updating layerCode validates uniqueness and blank values, supports swapping, and updates suggestions
    assert client.put(
        f"/api/contracts/{pid}/layers",
        json={"layers": [{"nodeId": layers[0]["nodeId"], "layerCode": "   "}]},
    ).status_code == 422
    assert client.put(
        f"/api/contracts/{pid}/layers",
        json={"layers": [{"nodeId": layers[0]["nodeId"], "layerCode": "cat-2"}]},
    ).status_code == 422
    swapped = client.put(
        f"/api/contracts/{pid}/layers",
        json={
            "layers": [
                {"nodeId": layers[0]["nodeId"], "layerCode": "CAT-2"},
                {"nodeId": layers[1]["nodeId"], "layerCode": "L1"},
            ]
        },
    )
    assert swapped.status_code == 200, swapped.text
    assert [l["layerCode"] for l in swapped.json()] == ["CAT-2", "L1", "L2"]

    # Saving a graph with duplicate layerCode via Structuring Studio is rejected with 422
    graph = client.get(f"/api/contracts/{pid}/dossier").json()["contract"]["graph"]
    for n in graph["nodes"]:
        if n["nodeId"] == layers[1]["nodeId"]:
            n["params"]["layerCode"] = "L2"
    assert client.put(f"/api/programmes/{pid}/graph", json=graph).status_code == 422

    # Removing the new layer removes it from Structuring Studio suggestions
    assert client.delete(f"/api/contracts/{pid}/layers/{new_layer['nodeId']}").status_code == 200
    tpls_after = client.get(f"/api/graph-templates?programmeId={pid}").json()
    cms_layers_after = [t for t in tpls_after if t.get("source") == "CMS" and t["templateId"].startswith(f"cms-layer-{pid}-")]
    assert [t["layerCode"] for t in cms_layers_after] == ["CAT-2", "L1"]

