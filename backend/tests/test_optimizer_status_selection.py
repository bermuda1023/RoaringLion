"""The Optimizer builds portfolios from deals of any status the user selects (declined included),
keeps the per-mode defaults when none is selected, and lists candidate deals scoped, searched and paged."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.engine.optimizer import (
    OptimizerRequest,
    _build_vectors,
    default_optimizer_statuses,
    resolve_optimizer_statuses,
)
from app.main import CONTRACT_STORE, SIM_STORE, _base_yelt, _ind_yelt, app
from app.models.enums import ProgrammeStatus, StructureType
from app.services.contracts import contract_to_programme_deal

client = TestClient(app)

S = ProgrammeStatus
CEDENT_TAG = "Statusfilter"


def _create(name: str, status: str, **extra) -> str:
    res = client.post(
        "/api/contracts",
        json={
            "cedentName": f"{CEDENT_TAG} {name} Re",
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
    return res.json()["programmeId"]


@pytest.fixture(scope="module")
def deals() -> dict[str, str]:
    """One deal per status of interest. Deals that today's defaults include are parked as DECLINED
    afterwards so they cannot leak into other test modules' default optimizer runs."""
    ids = {
        "declined": _create("Declined", "DECLINED"),
        "ntu": _create("Ntu", "NTU"),
        "expired": _create("Expired", "EXPIRED"),
        "authorized": _create("Authorized", "AUTHORIZED"),
        "quoted": _create("Quoted", "QUOTED"),
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
