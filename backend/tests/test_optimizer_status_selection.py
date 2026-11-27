"""The Optimizer builds portfolios from deals of any status the user selects (declined included),
keeps the per-mode defaults when none is selected, and lists candidate deals scoped, searched and paged."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.engine.optimizer import (
    OptimizerRequest,
    _build_vectors,
    _evaluate_for_rollup,
    default_optimizer_statuses,
    list_optimizable_layers,
    resolve_optimizer_statuses,
)
from app.engine.slice_runner import evaluate_programme_dag
from app.main import CONTRACT_STORE, SIM_STORE, _base_yelt, _ind_yelt, app
from app.models.domain import GraphEdgeSpec, GraphNodeSpec, ProgrammeDeal
from app.models.enums import NodeType, ProgrammeStatus, StructureType, ViewOfRisk
from app.services.contracts import contract_to_programme_deal
from app.services.lineage import LineageRegistry

client = TestClient(app)

S = ProgrammeStatus
CEDENT_TAG = "Statusfilter"
# Deals made by individual tests carry another tag so the paging tests can count the fixture's deals.
EXTRA_TAG = "Statusextra"


def _create(name: str, status: str, tag: str = EXTRA_TAG, **extra) -> str:
    return _create_full(name, status, tag, **extra)["programmeId"]


def _create_full(name: str, status: str, tag: str = EXTRA_TAG, **extra) -> dict:
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": f"{tag} {name} Re",
            "programmeName": f"{name} Cat XL",
            "status": status,
            "limit": 20.0,
            "attachment": 40.0,
            "depositPremium": 3.0,
            "signedLine": 0.5,
            "regionPerils": ["US-FL-WS"],
            "perils": ["WS"],
            **extra,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _yelts():
    return SIM_STORE.get_yelt(_base_yelt.sim_set_id), SIM_STORE.get_yelt(_ind_yelt.sim_set_id)


@pytest.fixture(scope="module")
def deals() -> dict[str, str]:
    """One deal per status of interest. Deals that today's defaults include are parked as DECLINED
    afterwards so they cannot leak into other test modules' default optimizer runs."""
    ids = {
        "declined": _create("Declined", "DECLINED", CEDENT_TAG),
        "ntu": _create("Ntu", "NTU", CEDENT_TAG),
        "expired": _create("Expired", "EXPIRED", CEDENT_TAG),
        "authorized": _create("Authorized", "AUTHORIZED", CEDENT_TAG),
        "quoted": _create("Quoted", "QUOTED", CEDENT_TAG),
    }
    yield ids
    for key in ("authorized", "quoted"):
        assert client.put(f"/api/contracts/{ids[key]}", json={"status": "DECLINED"}).status_code == 200


def _explore(**body) -> dict:
    res = client.post("/api/optimizer/explore?num_points=4", json={"granularity": "LAYER", **body})
    assert res.status_code == 200, res.text
    return res.json()


def _programmes(layers: list[dict]) -> set[str]:
    return {l["programmeId"] for l in layers}


def _candidates(**params) -> dict:
    res = client.get("/api/optimizer/candidates", params=params)
    assert res.status_code == 200, res.text
    return res.json()


# ── Defaults ──


def test_default_statuses_match_the_previous_hard_coded_rules():
    assert default_optimizer_statuses("CURRENT") == [S.QUOTED, S.FIRM_ORDER, S.BOUND]
    assert default_optimizer_statuses("FORWARD_PLANNING") == [
        s for s in S if s not in (S.DECLINED, S.NTU, S.EXPIRED)
    ]
    assert resolve_optimizer_statuses("CURRENT", None) == default_optimizer_statuses("CURRENT")
    # An explicit selection wins over the mode, is de-duplicated and comes back in enum order.
    assert resolve_optimizer_statuses("CURRENT", [S.DECLINED, S.BOUND, S.DECLINED]) == [S.BOUND, S.DECLINED]


def test_forward_planning_default_still_leaves_out_declined_ntu_and_expired(deals):
    body = _explore(mode="FORWARD_PLANNING")
    progs = _programmes(body["initialLayers"])
    assert {deals["authorized"], deals["quoted"], "prog-farmers-us-27"} <= progs
    assert not progs & {deals["declined"], deals["ntu"], deals["expired"], "prog-farmers-us-26"}
    assert body["includedStatuses"] == [s.value for s in default_optimizer_statuses("FORWARD_PLANNING")]
    assert {l["status"] for l in body["initialLayers"]} <= set(body["includedStatuses"]) | {"PLANNED"}


def test_current_mode_default_still_means_bound_quoted_and_firm_order(deals):
    body = _explore(mode="CURRENT")
    progs = _programmes(body["initialLayers"])
    assert {deals["quoted"], "prog-farmers-us-27"} <= progs
    assert not progs & {deals["authorized"], deals["declined"], deals["ntu"], deals["expired"]}
    assert body["includedStatuses"] == ["QUOTED", "FIRM_ORDER", "BOUND"]


def test_default_selection_gives_what_the_previous_rule_gave(deals):
    """Before statuses were selectable the routes loaded every contract, dropped ILWs and declined, NTU
    and expired deals, and the engine narrowed CURRENT mode. Scoping in SQL must give the same layers."""
    off_risk = {S.DECLINED, S.NTU, S.EXPIRED}
    all_deals = {c.programme_id: contract_to_programme_deal(c) for c in CONTRACT_STORE.list_contracts()}
    previous_universe = [
        d for d in all_deals.values()
        if d.structure != StructureType.ILW and d.status not in off_risk
    ]
    yelt, ind_yelt = _yelts()
    for mode in ("CURRENT", "FORWARD_PLANNING"):
        expected = list_optimizable_layers(
            programmes=previous_universe,
            yelt=yelt,
            ind_yelt=ind_yelt,
            view=ViewOfRisk.ADJUSTED,
            mode=mode,
            all_programmes=all_deals,
        )
        got = client.get(f"/api/optimizer/layers?view=ADJUSTED&mode={mode}").json()
        assert {l["layerKey"]: l for l in got} == {l.layer_key: l.model_dump(by_alias=True, mode="json") for l in expected}
        assert len(got) == len(expected) > 1


def test_manifest_records_the_statuses_asked_for_and_the_ones_used(deals):
    default = client.post("/api/optimizer/solve", json={"mode": "CURRENT"}).json()["manifest"]["parameters"]
    assert default["included_statuses"] is None
    assert default["effective_statuses"] == ["QUOTED", "FIRM_ORDER", "BOUND"]

    explicit = client.post("/api/optimizer/solve", json={"mode": "CURRENT", "includedStatuses": ["NTU", "BOUND"]})
    assert explicit.json()["manifest"]["parameters"]["included_statuses"] == ["BOUND", "NTU"]
    assert explicit.json()["manifest"]["parameters"]["effective_statuses"] == ["BOUND", "NTU"]

    explored = _explore(mode="CURRENT", includedStatuses=["NTU", "BOUND"])["manifest"]["parameters"]
    assert explored["included_statuses"] == ["BOUND", "NTU"]
    assert explored["effective_statuses"] == ["BOUND", "NTU"]
    assert _explore(mode="CURRENT")["manifest"]["parameters"]["included_statuses"] is None


# ── Any status can be selected ──


def test_declined_deals_enter_the_portfolio_when_selected_and_carry_their_status(deals):
    body = _explore(mode="FORWARD_PLANNING", includedStatuses=["BOUND", "DECLINED"])
    assert body["includedStatuses"] == ["BOUND", "DECLINED"]

    for layers in (body["initialLayers"], body["frontier"][body["recommendedIndex"]]["layers"]):
        by_prog = {l["programmeId"]: l for l in layers}
        assert by_prog[deals["declined"]]["status"] == "DECLINED"
        assert by_prog["prog-farmers-us-27"]["status"] == "BOUND"
        # Statuses that were not selected stay out, even ones the mode would include by default.
        assert not set(by_prog) & {deals["quoted"], deals["authorized"], deals["ntu"]}
        assert {l["status"] for l in layers if l["direction"] == "ASSUMED"} == {"BOUND", "DECLINED"}

    # The declined deal is a real decision variable: it starts at its recorded line and has economics.
    declined = next(l for l in body["initialLayers"] if l["programmeId"] == deals["declined"])
    assert declined["currentShare"] == 0.5
    assert declined["optimalLimitM"] == pytest.approx(10.0)


def test_every_status_can_be_selected_in_either_mode(deals):
    every = [s.value for s in S]
    for mode in ("CURRENT", "FORWARD_PLANNING"):
        body = _explore(mode=mode, includedStatuses=every)
        by_prog = {l["programmeId"]: l["status"] for l in body["initialLayers"]}
        assert by_prog[deals["declined"]] == "DECLINED"
        assert by_prog[deals["ntu"]] == "NTU"
        assert by_prog[deals["expired"]] == "EXPIRED"
        assert by_prog[deals["authorized"]] == "AUTHORIZED"
        assert by_prog["prog-farmers-us-26"] == "EXPIRED"


def test_a_portfolio_can_be_built_from_declined_deals_only(deals):
    res = client.post(
        "/api/optimizer/solve",
        json={"mode": "CURRENT", "granularity": "DEAL", "includedStatuses": ["DECLINED"], "includeRetroIlwCandidate": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["includedStatuses"] == ["DECLINED"]
    assert deals["declined"] in {d["programmeId"] for d in body["deals"]}
    assert {d["status"] for d in body["deals"]} == {"DECLINED"}
    # Lineage records which statuses the portfolio was built from.
    assert body["manifest"]["parameters"]["included_statuses"] == ["DECLINED"]


def test_frontier_sweep_accepts_the_status_selection(deals):
    res = client.post(
        "/api/optimizer/frontier?num_points=2",
        json={"mode": "FORWARD_PLANNING", "includedStatuses": ["BOUND", "DECLINED", "NTU"]},
    )
    assert res.status_code == 200, res.text
    assert len(res.json()) == 2


def test_selecting_a_status_does_not_change_any_deal_status(deals):
    before = {c["programmeId"]: c["status"] for c in client.get("/api/contracts").json()}
    _explore(mode="FORWARD_PLANNING", includedStatuses=["DECLINED", "NTU", "EXPIRED", "BOUND"])
    after = {c["programmeId"]: c["status"] for c in client.get("/api/contracts").json()}
    assert after == before
    assert after[deals["declined"]] == "DECLINED"


# ── Deals recorded at a 0% line (usual for declined and NTU deals) ──


def test_a_deal_at_a_zero_line_keeps_its_risk_and_starts_from_zero_share():
    """Losses are restated to 100% by dividing by the placed share. At a 0% line that used to leave a deal
    with premium and no loss, which the optimizer would have treated as free profit."""
    terms = {"limit": 22.0, "attachment": 41.0, "depositPremium": 3.3}
    zero = _create("ZeroLine", "DECLINED", **terms, signedLine=0.0, writtenLine=0.0)
    half = _create("HalfLine", "DECLINED", **terms, signedLine=0.5)

    rows = client.get("/api/optimizer/layers?includedStatuses=DECLINED").json()
    by_prog = {r["programmeId"]: r for r in rows}
    assert by_prog[zero]["currentShare"] == 0.0 and by_prog[half]["currentShare"] == 0.5
    assert by_prog[zero]["el1xM"] == by_prog[half]["el1xM"] > 0.1
    for field in ("limit1xM", "premium1xM", "expectedProfit1xM", "rolPct"):
        assert by_prog[zero][field] == by_prog[half][field]
    assert by_prog[zero]["expectedProfit1xM"] < by_prog[zero]["premium1xM"]

    body = _explore(includedStatuses=["DECLINED"], includeRetroIlwCandidate=False)
    start = {l["programmeId"]: l for l in body["initialLayers"]}
    assert start[zero]["optimalShare"] == 0.0 and start[zero]["optimalLimitM"] == 0.0 and start[zero]["optimalElM"] == 0.0
    best = {l["programmeId"]: l for l in body["frontier"][body["recommendedIndex"]]["layers"]}
    if best[zero]["optimalShare"] > 0:
        assert best[zero]["optimalElM"] == pytest.approx(best[zero]["optimalShare"] * by_prog[zero]["el1xM"], abs=1e-3)

    # Deal granularity has no current mix to scale either: 100% is 100% of the deal's layers.
    solved = client.post(
        "/api/optimizer/solve",
        json={"granularity": "DEAL", "includedStatuses": ["DECLINED"], "includeRetroIlwCandidate": False},
    ).json()
    deal = next(d for d in solved["deals"] if d["programmeId"] == zero)
    assert deal["currentShare"] == 0.0
    assert deal["optimalLimitM"] == pytest.approx(deal["optimalShare"] * 22.0, abs=1e-3)
    assert deal["optimalElM"] == pytest.approx(deal["optimalShare"] * by_prog[zero]["el1xM"], abs=1e-3)


def test_restating_a_zero_line_layer_leaves_the_rest_of_the_programme_alone():
    full = _create_full(
        "MixedLines",
        "NTU",
        layers=[
            {"limit": 20, "attachment": 40, "depositPremium": 3.0, "signedLine": 0.0},
            {"limit": 20, "attachment": 60, "depositPremium": 2.0, "signedLine": 0.5},
        ],
    )
    programme = contract_to_programme_deal(CONTRACT_STORE.get_contract(full["programmeId"]))
    zero_layer, held_layer = [n.node_id for n in programme.graph.nodes if n.node_type.value == "OCC_XL"]
    yelt, _ = _yelts()

    _, plain = evaluate_programme_dag(programme, yelt, LineageRegistry(persist=False), views=[ViewOfRisk.ADJUSTED])
    restated, ann, _, unplaced = _evaluate_for_rollup(programme, yelt, ViewOfRisk.ADJUSTED)

    assert unplaced == {zero_layer}
    assert float(plain["annual"]["ADJUSTED"][zero_layer].sum()) == 0.0
    assert float(ann[zero_layer].mean()) > 0.1
    assert (ann[f"px-{zero_layer}"] == ann[zero_layer]).all()
    for node_id in (held_layer, f"px-{held_layer}", f"out-{programme.programme_id}"):
        assert (ann[node_id] == plain["annual"]["ADJUSTED"][node_id]).all()
    shares = {n.node_id: n.params.get("placedShare") for n in restated.graph.nodes}
    assert shares[zero_layer] == 1.0 and shares[held_layer] == 0.5
    # The stored programme is untouched.
    assert CONTRACT_STORE.get_contract(full["programmeId"]).graph == programme.graph


class _TwoLayerDeal:
    """A stored deal with layer `zero` (20 xs 40, premium 3) at a 0% line and layer `held` (20 xs 60,
    premium 2) at 50%, plus helpers to reshape its graph in memory and read what the optimizer builds."""

    def __init__(self) -> None:
        full = _create_full(
            "Shapes",
            "DECLINED",
            layers=[
                {"limit": 20, "attachment": 40, "depositPremium": 3.0, "signedLine": 0.0},
                {"limit": 20, "attachment": 60, "depositPremium": 2.0, "signedLine": 0.5},
            ],
        )
        self.deal = contract_to_programme_deal(CONTRACT_STORE.get_contract(full["programmeId"]))
        self.zero, self.held = [n.node_id for n in self.deal.graph.nodes if n.node_type == NodeType.OCC_XL]
        self.filter = next(n.node_id for n in self.deal.graph.nodes if n.node_type == NodeType.REGION_PERIL_FILTER)
        self.yelt, self.ind_yelt = _yelts()
        # True 100% expected loss of each layer, from the graph with both layers at a 100% line.
        at_full = self.annual(self.reshape(shares={self.zero: 1.0, self.held: 1.0}))
        self.el_zero, self.el_held = float(at_full[self.zero].mean()), float(at_full[self.held].mean())
        assert self.el_zero > 0.1 and self.el_held > 0.1

    def reshape(
        self, *, shares=None, params=None, drop_nodes=(), drop_edges=(), add_nodes=(), add_edges=(), **deal_fields
    ) -> ProgrammeDeal:
        changes = {node_id: dict(extra) for node_id, extra in (params or {}).items()}
        for node_id, share in (shares or {}).items():
            changes.setdefault(node_id, {})["placedShare"] = share
        nodes = [
            n.model_copy(update={"params": {**n.params, **changes[n.node_id]}}) if n.node_id in changes else n
            for n in self.deal.graph.nodes
            if n.node_id not in drop_nodes
        ]
        edges = [
            e
            for e in self.deal.graph.edges
            if (e.source, e.target) not in drop_edges and e.source not in drop_nodes and e.target not in drop_nodes
        ]
        edges += [
            GraphEdgeSpec(edge_id=f"e-test-{i}", source=src, target=dst, sign=1, weight=1.0)
            for i, (src, dst) in enumerate(add_edges)
        ]
        graph = self.deal.graph.model_copy(update={"nodes": [*nodes, *add_nodes], "edges": edges})
        return self.deal.model_copy(update={"graph": graph, **deal_fields})

    def annual(self, deal: ProgrammeDeal) -> dict:
        _, out = evaluate_programme_dag(deal, self.yelt, LineageRegistry(persist=False), views=[ViewOfRisk.ADJUSTED])
        return out["annual"]["ADJUSTED"]

    def items(self, deal: ProgrammeDeal, granularity: str = "LAYER") -> dict[str, dict]:
        request = OptimizerRequest(
            granularity=granularity, include_retro_ilw_candidate=False, included_statuses=[S.DECLINED]
        )
        built, _ = _build_vectors([deal], self.yelt, self.ind_yelt, request)
        return {it["layer_id"]: it for it in built}


@pytest.fixture(scope="module")
def shapes() -> _TwoLayerDeal:
    return _TwoLayerDeal()


def test_zero_line_layer_on_a_graph_without_pricing_links(shapes):
    pricing_nodes = [n.node_id for n in shapes.deal.graph.nodes if n.node_type == NodeType.PRICING_OUTPUT]
    items = shapes.items(shapes.reshape(drop_nodes=pricing_nodes))
    assert items[shapes.zero]["current_share"] == 0.0
    assert items[shapes.zero]["el_1x"] == pytest.approx(shapes.el_zero)
    assert (items[shapes.zero]["prem_1x"], items[shapes.zero]["limit_1x"]) == (3.0, 20.0)
    assert items[shapes.held]["current_share"] == 0.5
    assert items[shapes.held]["el_1x"] == pytest.approx(shapes.el_held)


def test_zero_line_layer_priced_through_a_combination(shapes):
    combo = GraphNodeSpec(node_id="combo", node_type=NodeType.COMBINATION, label="Combination", params={})
    deal = shapes.reshape(
        drop_edges=[(shapes.zero, f"px-{shapes.zero}")],
        add_nodes=[combo],
        add_edges=[(shapes.zero, "combo"), ("combo", f"px-{shapes.zero}")],
    )
    items = shapes.items(deal)
    assert items[shapes.zero]["current_share"] == 0.0
    assert items[shapes.zero]["el_1x"] == pytest.approx(shapes.el_zero)


def test_zero_line_layer_sharing_a_pricing_output_with_a_held_layer(shapes):
    """Several inputs into one Pricing Output count together as 100% of the linked layer; the unplaced
    one now contributes its 100% loss instead of nothing."""
    deal = shapes.reshape(drop_nodes=[f"px-{shapes.held}"], add_edges=[(shapes.held, f"px-{shapes.zero}")])
    items = shapes.items(deal)
    assert set(items) == {shapes.zero}
    assert items[shapes.zero]["el_1x"] == pytest.approx(shapes.el_zero + 0.5 * shapes.el_held)


def test_zero_line_layer_fed_by_another_zero_line_layer(shapes):
    # The second layer now reads the first layer's recoveries (at most 20 an event), so it attaches low.
    chained = dict(
        shares={shapes.held: 0.0},
        params={shapes.held: {"occLimit": 10.0, "occAttachment": 5.0}},
        drop_edges=[(shapes.filter, shapes.held)],
        add_edges=[(shapes.zero, shapes.held)],
    )
    items = shapes.items(shapes.reshape(**chained))
    both_full = shapes.annual(shapes.reshape(**{**chained, "shares": {shapes.zero: 1.0, shapes.held: 1.0}}))
    assert float(both_full[shapes.held].mean()) > 0.01
    assert items[shapes.held]["current_share"] == 0.0
    assert items[shapes.held]["el_1x"] == pytest.approx(float(both_full[shapes.held].mean()))
    assert items[shapes.zero]["el_1x"] == pytest.approx(shapes.el_zero)


@pytest.mark.parametrize("sub_shares, programme_line", [((0.0, 0.0), 0.5), ((None, None), 0.0), ((0.0, 0.5), 0.5)])
def test_zero_line_cascading_tower(shapes, sub_shares, programme_line):
    """The rollup reads a tower as one layer at its first sub-layer's share (the programme line when
    the sub-layer names none), so that is what decides whether the tower is unplaced."""
    def tower(shares):
        subs = [
            {"occLimit": 20.0, "occAttachment": 40.0, "depositPremium": 3.0},
            {"occLimit": 20.0, "occAttachment": 60.0, "depositPremium": 2.0},
        ]
        layers = [{**sub, **({} if share is None else {"placedShare": share})} for sub, share in zip(subs, shares)]
        return GraphNodeSpec(node_id=shapes.zero, node_type=NodeType.CASCADING_TOWER, label="Tower", params={"layers": layers})

    def with_tower(shares, **fields):
        return shapes.reshape(
            drop_nodes=[shapes.zero, shapes.held, f"px-{shapes.held}"],
            add_nodes=[tower(shares)],
            add_edges=[(shapes.filter, shapes.zero), (shapes.zero, f"px-{shapes.zero}")],
            **fields,
        )

    full_el = float(shapes.annual(with_tower((1.0, 1.0)))[shapes.zero].mean())
    assert full_el > 0.1
    item = shapes.items(with_tower(sub_shares, signed_line=programme_line))[shapes.zero]
    assert item["current_share"] == 0.0
    assert item["el_1x"] == pytest.approx(full_el)
    assert (item["prem_1x"], item["limit_1x"]) == (5.0, 40.0)


def test_deal_granularity_when_no_layer_is_held(shapes):
    """The contract-level line can say 50% while every layer is at 0%; layers are what roll up."""
    deal = shapes.reshape(shares={shapes.held: 0.0}, signed_line=0.5)
    item = shapes.items(deal, "DEAL")[shapes.deal.programme_id]
    assert item["current_share"] == 0.0
    assert item["el_1x"] == pytest.approx(shapes.el_zero + shapes.el_held)
    assert (item["prem_1x"], item["limit_1x"]) == (5.0, 40.0)
    assert item["profit_1x"] < item["prem_1x"]


# ── Invalid selections ──


def test_bad_status_selections_are_422():
    assert client.post("/api/optimizer/explore", json={"includedStatuses": []}).status_code == 422
    assert client.post("/api/optimizer/solve", json={"includedStatuses": ["WON"]}).status_code == 422
    assert client.get("/api/optimizer/candidates?includedStatuses=WON").status_code == 422
    assert client.get("/api/optimizer/candidates?mode=BOGUS").status_code == 422
    assert client.get("/api/optimizer/candidates?limit=101").status_code == 422
    assert client.get("/api/optimizer/candidates?offset=-1").status_code == 422


def test_a_selection_matching_no_deal_is_a_clear_422_not_a_500():
    assert not CONTRACT_STORE.query_contracts(statuses=[S.BOUND_FOR_OPS])[0]
    for path in ("/api/optimizer/solve", "/api/optimizer/explore", "/api/optimizer/frontier"):
        res = client.post(path, json={"includedStatuses": ["BOUND_FOR_OPS"]})
        assert res.status_code == 422, path
        assert "No programmes match the selected deal statuses" in res.json()["detail"]


# ── Scoped, searchable, paged selection ──


def test_candidates_report_defaults_counts_and_each_deals_status(deals):
    page = _candidates(mode="FORWARD_PLANNING", limit=100)
    assert page["usesDefaultStatuses"] is True
    assert page["includedStatuses"] == page["defaultStatuses"]
    assert "DECLINED" not in page["includedStatuses"]

    counts = {c["status"]: c for c in page["statusCounts"]}
    assert list(counts) == [s.value for s in S]
    for status in ("DECLINED", "NTU", "BOUND"):
        expected = [
            c for c in client.get(f"/api/contracts?status={status}").json() if c["structure"] != "ILW"
        ]
        assert counts[status]["programmes"] == len(expected)
    assert counts["DECLINED"]["included"] is False and counts["DECLINED"]["defaultIncluded"] is False
    assert counts["BOUND"]["included"] is True and counts["BOUND"]["defaultIncluded"] is True
    assert page["universeProgrammes"] == sum(c["programmes"] for c in counts.values() if c["included"])
    assert page["matchedProgrammes"] == page["universeProgrammes"]

    assert deals["declined"] not in page["programmeIds"]
    assert all(l["direction"] == "ASSUMED" for l in page["layers"])
    assert page["retroCandidate"]["layerKey"] == "cand-retro-ilw-fl"

    with_declined = _candidates(mode="FORWARD_PLANNING", includedStatuses=["DECLINED", "BOUND"], limit=100)
    assert with_declined["usesDefaultStatuses"] is False
    by_prog = {l["programmeId"]: l["status"] for l in with_declined["layers"]}
    assert by_prog[deals["declined"]] == "DECLINED"
    assert set(by_prog.values()) == {"DECLINED", "BOUND"}


def test_candidates_are_paged_by_programme(deals):
    params = {"mode": "FORWARD_PLANNING", "includedStatuses": ["DECLINED", "NTU", "EXPIRED"], "search": CEDENT_TAG}
    first = _candidates(**params, limit=2, offset=0)
    second = _candidates(**params, limit=2, offset=2)
    assert first["matchedProgrammes"] == second["matchedProgrammes"] == 3
    assert len(first["programmeIds"]) == 2 and len(second["programmeIds"]) == 1
    assert not set(first["programmeIds"]) & set(second["programmeIds"])
    assert set(first["programmeIds"]) | set(second["programmeIds"]) == {deals["declined"], deals["ntu"], deals["expired"]}
    # Only the page's programmes are evaluated into layers.
    assert _programmes(first["layers"]) == set(first["programmeIds"])
    assert _candidates(**params, limit=2, offset=10)["programmeIds"] == []


def test_candidate_search_is_scoped_to_the_selected_statuses(deals):
    hit = _candidates(includedStatuses=["DECLINED"], search="statusFILTER declined")
    assert hit["programmeIds"] == [deals["declined"]]
    assert hit["matchedProgrammes"] == 1
    assert hit["universeProgrammes"] >= 1

    # Same search, status not selected: nothing comes back.
    assert _candidates(includedStatuses=["BOUND"], search="statusfilter declined")["programmeIds"] == []
    # Searching by programme id works, and SQL wildcards in the term are literal.
    assert _candidates(includedStatuses=["DECLINED"], search=deals["declined"])["programmeIds"] == [deals["declined"]]
    assert _candidates(includedStatuses=["DECLINED"], search="%")["matchedProgrammes"] == 0


def test_optimizer_never_loads_the_whole_book(deals, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("optimizer selection must be scoped in SQL, not load every contract")

    monkeypatch.setattr(CONTRACT_STORE, "list_contracts", boom)
    assert client.get("/api/optimizer/candidates?limit=1").status_code == 200
    assert client.get("/api/optimizer/layers?includedStatuses=DECLINED").status_code == 200
    assert client.post("/api/optimizer/explore?num_points=4", json={"includedStatuses": ["DECLINED"]}).status_code == 200
    assert client.post("/api/optimizer/solve", json={"mode": "CURRENT"}).status_code == 200


def test_contract_store_filters_pages_and_counts_in_sql(deals):
    rows, total = CONTRACT_STORE.query_contracts(
        statuses=[S.DECLINED, S.NTU, S.EXPIRED], search=CEDENT_TAG, order_by="NAME", limit=2
    )
    assert total == 3
    assert [c.cedent_name for c in rows] == [f"{CEDENT_TAG} Declined Re", f"{CEDENT_TAG} Expired Re"]
    assert CONTRACT_STORE.query_contracts(statuses=[]) == ([], 0)
    assert CONTRACT_STORE.query_contracts(programme_ids=[]) == ([], 0)
    by_id, n = CONTRACT_STORE.query_contracts(programme_ids=[deals["ntu"], "missing"])
    assert n == 1 and by_id[0].programme_id == deals["ntu"]

    counts = CONTRACT_STORE.count_by_status(exclude_structures=[StructureType.ILW])
    everything = [c for c in CONTRACT_STORE.list_contracts() if c.structure != StructureType.ILW]
    assert sum(counts.values()) == len(everything)
    assert counts[S.NTU] == sum(1 for c in everything if c.status == S.NTU)


def test_search_escapes_wildcards_and_ignores_case_beyond_ascii():
    accented = _create("Élan_Münchener 100% Rück", "DECLINED")
    plain = _create("Elan Munchener 100 Ruck", "DECLINED")

    def found(term: str) -> list[str]:
        return [c.programme_id for c in CONTRACT_STORE.query_contracts(statuses=[S.DECLINED], search=term, order_by="NAME")[0]]

    assert found("élan_münchener") == [accented]
    assert found("ÉLAN_MÜNCHENER 100% RÜCK") == [accented]
    # `_` and `%` are literal, so they do not match the plain-ASCII twin.
    assert found("n_M") == [accented]
    assert found("100%") == [accented]
    assert found("100") == [plain, accented] or found("100") == [accented, plain]
    assert found("\\") == []
    assert _candidates(includedStatuses=["DECLINED"], search="MÜNCHENER")["programmeIds"] == [accented]


def test_recent_order_is_deterministic_and_pages_without_gaps(deals):
    everything, total = CONTRACT_STORE.query_contracts(statuses=[S.DECLINED, S.NTU, S.EXPIRED])
    ids = [c.programme_id for c in everything]
    assert total == len(ids) == len(set(ids)) >= 3
    assert ids == [c.programme_id for c in CONTRACT_STORE.query_contracts(statuses=[S.DECLINED, S.NTU, S.EXPIRED])[0]]
    paged: list[str] = []
    for offset in range(0, total, 2):
        rows, n = CONTRACT_STORE.query_contracts(statuses=[S.DECLINED, S.NTU, S.EXPIRED], limit=2, offset=offset)
        assert n == total
        paged += [c.programme_id for c in rows]
    assert paged == ids


def test_ilw_contracts_stay_out_of_the_optimizer_whatever_their_status():
    ilw = _create("IlwStructure", "DECLINED", structure="ILW")
    page = _candidates(includedStatuses=["DECLINED"], search=f"{EXTRA_TAG} IlwStructure")
    assert page["programmeIds"] == [] and page["matchedProgrammes"] == 0
    assert ilw not in _programmes(client.get("/api/optimizer/layers?includedStatuses=DECLINED").json())
    declined = [c for c in client.get("/api/contracts?status=DECLINED").json() if c["structure"] != "ILW"]
    counts = {c["status"]: c["programmes"] for c in _candidates()["statusCounts"]}
    assert counts["DECLINED"] == len(declined)


def test_legacy_layers_endpoint_accepts_the_status_selection(deals):
    default_keys = {l["programmeId"] for l in client.get("/api/optimizer/layers?mode=FORWARD_PLANNING").json()}
    assert deals["declined"] not in default_keys
    picked = client.get("/api/optimizer/layers?mode=FORWARD_PLANNING&includedStatuses=DECLINED").json()
    assert {l["status"] for l in picked if l["direction"] == "ASSUMED"} == {"DECLINED"}
    assert deals["declined"] in {l["programmeId"] for l in picked}


# ── Pricing Output links to a contract in another status ──


def test_linked_layer_follows_the_status_selection(deals):
    """A graph on one programme can price another contract's layer; that layer rolls up under the
    linked contract's status, so it obeys the same status selection."""
    target = contract_to_programme_deal(CONTRACT_STORE.get_contract(deals["declined"]))
    linker = target.model_copy(update={"programme_id": "linker-not-stored", "status": S.BOUND})
    yelt, ind_yelt = SIM_STORE.get_yelt(_base_yelt.sim_set_id), SIM_STORE.get_yelt(_ind_yelt.sim_set_id)

    def linked_statuses(target_status: ProgrammeStatus, **req) -> list[str]:
        lookup = {target.programme_id: target.model_copy(update={"status": target_status})}
        request = OptimizerRequest(granularity="LAYER", include_retro_ilw_candidate=False, **req)
        items, _ = _build_vectors([linker], yelt, ind_yelt, request, lookup)
        assert all(it["programme_id"] == target.programme_id for it in items)
        return [it["status"] for it in items]

    # Defaults resolve links exactly as before: any target that is not declined, NTU or expired.
    assert linked_statuses(S.DECLINED, mode="FORWARD_PLANNING") == []
    assert linked_statuses(S.DECLINED, mode="CURRENT") == []
    assert linked_statuses(S.AUTHORIZED, mode="CURRENT") == ["AUTHORIZED"]
    # With an explicit selection the linked contract must be in a selected status.
    assert linked_statuses(S.DECLINED, included_statuses=[S.BOUND]) == []
    assert linked_statuses(S.AUTHORIZED, included_statuses=[S.BOUND]) == []
    assert linked_statuses(S.DECLINED, included_statuses=[S.BOUND, S.DECLINED]) == ["DECLINED"]


def test_stored_cross_status_link_resolves_as_before_by_default_and_follows_an_explicit_selection():
    """A QUOTED programme's graph prices an AUTHORIZED contract's layer. CURRENT mode has always rolled
    that layer up (the route now fetches the linked contract by id); an explicit selection that leaves
    AUTHORIZED out drops it, and says it is not the default."""
    modelled = _create_full("LinkModelled", "QUOTED")
    priced = _create_full("LinkPriced", "AUTHORIZED", limit=30.0, depositPremium=6.0)
    try:
        priced_layer = next(n["nodeId"] for n in priced["graph"]["nodes"] if n["nodeType"] == "OCC_XL")
        for n in priced["graph"]["nodes"]:
            if n["nodeType"] == "PRICING_OUTPUT":
                n["params"] = {"unlinked": True}
        assert client.put(f"/api/programmes/{priced['programmeId']}/graph", json=priced["graph"]).status_code == 200
        graph = modelled["graph"]
        feeder = next(n["nodeId"] for n in graph["nodes"] if n["nodeType"] == "OCC_XL")
        graph["nodes"].append({
            "nodeId": "px-cross",
            "nodeType": "PRICING_OUTPUT",
            "label": "Pricing Output",
            "params": {"contractId": priced["programmeId"], "layerNodeId": priced_layer},
            "position": {"x": 900, "y": 260},
        })
        graph["edges"].append({"edgeId": "e-cross", "source": feeder, "target": "px-cross", "sign": 1, "weight": 1.0})
        assert client.put(f"/api/programmes/{modelled['programmeId']}/graph", json=graph).status_code == 200
        linked_key = f"{priced['programmeId']}::{priced_layer}"
        current_defaults = ["QUOTED", "FIRM_ORDER", "BOUND"]

        by_default = {l["layerKey"]: l for l in _explore(mode="CURRENT")["initialLayers"]}
        assert by_default[linked_key]["status"] == "AUTHORIZED"
        assert by_default[linked_key]["optimalLimitM"] == pytest.approx(30.0 * 0.5)

        explicit = _explore(mode="CURRENT", includedStatuses=current_defaults)
        assert linked_key not in {l["layerKey"] for l in explicit["initialLayers"]}
        assert {l["status"] for l in explicit["initialLayers"] if l["direction"] == "ASSUMED"} <= set(current_defaults)

        search = f"{EXTRA_TAG} LinkModelled"
        page = _candidates(mode="CURRENT", search=search)
        assert page["usesDefaultStatuses"] is True
        assert linked_key in {l["layerKey"] for l in page["layers"]}
        explicit_page = _candidates(mode="CURRENT", includedStatuses=current_defaults, search=search)
        assert explicit_page["usesDefaultStatuses"] is False
        assert explicit_page["includedStatuses"] == page["includedStatuses"]
        assert linked_key not in {l["layerKey"] for l in explicit_page["layers"]}
    finally:
        for c in (modelled, priced):
            assert client.put(f"/api/contracts/{c['programmeId']}", json={"status": "DECLINED"}).status_code == 200
