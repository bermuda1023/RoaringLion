"""RoaringLion FastAPI entrypoint — Phase 0 + Phase 1 (Persistent CMS, Parquet SimSets/ELT, ILW Library, Breadcrumbs)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.db.database import init_engine
from app.engine.ilw import evaluate_ilw_trial_payouts, price_ilw_with_portfolio_hedge
from app.engine.slice_runner import (
    evaluate_deterministic_run,
    evaluate_portfolio_rollup,
    evaluate_programme_dag,
    validate_dag_acyclic,
)
from app.models.domain import (
    DagEvalResponse,
    DeterministicRunRequest,
    DeterministicRunResponse,
    GraphSpec,
    PortfolioRollupResponse,
    ProgrammeDeal,
    RunManifest,
    UpliftRule,
)
from app.models.enums import ContractDirection, ProgrammeStatus, StructureType, ViewOfRisk
from app.models.phase1 import (
    ContractCreate,
    ContractFull,
    ContractUpdate,
    EltIngestRequest,
    IlwPriceRequest,
    IlwPriceResponse,
    SimSetDetail,
    SliceRollupRequest,
    StaleManifestInfo,
)
from app.services.contracts import ContractStore, contract_to_programme_deal
from app.services.lineage import LineageRegistry
from app.services.sim_sets import SimSetStore
from app.routes_extended import create_extended_router

app = FastAPI(
    title="RoaringLion Catastrophe Reinsurance Platform",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize persistent SQLite/Postgres store + Parquet simulation-set catalog
init_engine()
SIM_STORE = SimSetStore()
CONTRACT_STORE = ContractStore()
LINEAGE = LineageRegistry(persist=True)

_base_yelt, _ind_yelt = SIM_STORE.ensure_seeded(num_trials=100_000)
CONTRACT_STORE.ensure_seeded(default_sim_set_id=_base_yelt.sim_set_id)

# Convenience aliases kept for Phase 0 tests compatibility
SIM_SETS = {_base_yelt.sim_set_id: _base_yelt, _ind_yelt.sim_set_id: _ind_yelt}
PROGRAMMES = {c.programme_id: contract_to_programme_deal(c) for c in CONTRACT_STORE.list_contracts()}


def _sync_memory_aliases() -> None:
    PROGRAMMES.clear()
    for c in CONTRACT_STORE.list_contracts():
        PROGRAMMES[c.programme_id] = contract_to_programme_deal(c)


def _compute_bound_gross_loss_vector(view: ViewOfRisk) -> np.ndarray:
    bound = [
        c for c in CONTRACT_STORE.list_contracts(status=ProgrammeStatus.BOUND)
        if c.direction == ContractDirection.ASSUMED and c.structure != StructureType.ILW
    ]
    yelt = SIM_STORE.get_yelt(_base_yelt.sim_set_id)
    total = np.zeros(yelt.num_trials, dtype=np.float64)
    for c in bound:
        prog = contract_to_programme_deal(c)
        _, vecs = evaluate_programme_dag(prog, yelt, LineageRegistry(persist=False), views=[view])
        for node in prog.graph.nodes:
            if node.node_type.value in ("OCC_XL", "AGG_XL"):
                total += vecs["annual"][view.value][node.node_id]
    return total


@app.get("/api/health")
def health() -> dict:
    sims = SIM_STORE.list_sim_sets()
    contracts = CONTRACT_STORE.list_contracts()
    return {
        "status": "ok",
        "engine": "roaring_lion_engine (rust-rayon)",
        "simSetsLoaded": len([s for s in sims if not s.is_industry_catalog]),
        "industryCatalogsLoaded": len([s for s in sims if s.is_industry_catalog]),
        "programmesLoaded": len(contracts),
    }


# ─────────────────────────── Simulation Sets (YELT / ELT / Industry) ───────────────────────────


@app.get("/api/sim-sets", response_model=list[SimSetDetail])
def list_sim_sets(industry_only: bool | None = Query(default=None, alias="industryOnly")) -> list[SimSetDetail]:
    return SIM_STORE.list_sim_sets(industry_only=industry_only)


@app.post("/api/sim-sets/ingest/yelt", response_model=SimSetDetail)
async def ingest_yelt_upload(
    file: UploadFile = File(...),
    name: str = Form(...),
    event_catalog_id: str = Form("SYNTH_CAT_V1"),
    num_trials: int = Form(10_000),
    currency: str = Form("USD"),
    trial_col: str = Form("trial_id"),
    event_col: str = Form("event_id"),
    loss_col: str = Form("loss_amount"),
    day_col: str | None = Form(None),
    region_peril_col: str | None = Form(None),
    default_region_peril: str = Form("US-FL-WS"),
    is_industry_catalog: bool = Form(False),
) -> SimSetDetail:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file upload")
    detail = SIM_STORE.ingest_yelt_bytes(
        raw_bytes=raw,
        filename=file.filename or "upload.csv",
        name=name,
        event_catalog_id=event_catalog_id,
        num_trials=num_trials,
        currency=currency,
        trial_col=trial_col,
        event_col=event_col,
        loss_col=loss_col,
        day_col=day_col,
        region_peril_col=region_peril_col,
        default_region_peril=default_region_peril,
        is_industry_catalog=is_industry_catalog,
        lineage=LINEAGE,
    )
    SIM_SETS[detail.sim_set_id] = SIM_STORE.get_yelt(detail.sim_set_id)
    return detail


@app.post("/api/sim-sets/ingest/elt", response_model=SimSetDetail)
def ingest_elt(req: EltIngestRequest) -> SimSetDetail:
    if not req.rows:
        raise HTTPException(status_code=422, detail="ELT must contain at least one event row")
    detail = SIM_STORE.ingest_elt(req, LINEAGE)
    SIM_SETS[detail.sim_set_id] = SIM_STORE.get_yelt(detail.sim_set_id)
    return detail


# ─────────────────────────── Contract Management System (CMS) ───────────────────────────


@app.get("/api/contracts", response_model=list[ContractFull])
def list_contracts(
    status: ProgrammeStatus | None = None,
    direction: ContractDirection | None = None,
    underwriter: str | None = None,
    office: str | None = None,
    renewal_period: str | None = Query(default=None, alias="renewalPeriod"),
    search: str | None = None,
) -> list[ContractFull]:
    return CONTRACT_STORE.list_contracts(
        status=status,
        direction=direction,
        underwriter=underwriter,
        office=office,
        renewal_period=renewal_period,
        search=search,
    )


@app.post("/api/contracts", response_model=ContractFull)
def create_contract(payload: ContractCreate) -> ContractFull:
    full = CONTRACT_STORE.create_contract(payload, LINEAGE)
    _sync_memory_aliases()
    return full


@app.put("/api/contracts/{programme_id}", response_model=ContractFull)
def update_contract(programme_id: str, patch: ContractUpdate) -> ContractFull:
    full = CONTRACT_STORE.update_contract(programme_id, patch)
    if not full:
        raise HTTPException(status_code=404, detail="Contract not found")
    _sync_memory_aliases()
    return full


@app.get("/api/programmes", response_model=list[ProgrammeDeal])
def list_programmes() -> list[ProgrammeDeal]:
    return [contract_to_programme_deal(c) for c in CONTRACT_STORE.list_contracts()]


@app.get("/api/programmes/{programme_id}", response_model=ProgrammeDeal)
def get_programme(programme_id: str) -> ProgrammeDeal:
    c = CONTRACT_STORE.get_contract(programme_id)
    if not c:
        raise HTTPException(status_code=404, detail="Programme not found")
    return contract_to_programme_deal(c)


@app.put("/api/programmes/{programme_id}/uplifts", response_model=ProgrammeDeal)
def update_programme_uplifts(programme_id: str, rules: list[UpliftRule]) -> ProgrammeDeal:
    c = CONTRACT_STORE.update_uplifts(programme_id, rules)
    if not c:
        raise HTTPException(status_code=404, detail="Programme not found")
    _sync_memory_aliases()
    return contract_to_programme_deal(c)


@app.post("/api/programmes/{programme_id}/evaluate", response_model=DagEvalResponse)
def evaluate_programme(programme_id: str) -> DagEvalResponse:
    c = CONTRACT_STORE.get_contract(programme_id)
    if not c:
        raise HTTPException(status_code=404, detail="Programme not found")
    prog = contract_to_programme_deal(c)
    yelt = SIM_STORE.get_yelt(prog.sim_set_id)
    ind_yelt = SIM_STORE.get_yelt(_ind_yelt.sim_set_id)
    resp, _ = evaluate_programme_dag(prog, yelt, LINEAGE, industry_yelt=ind_yelt)
    return resp


@app.put("/api/programmes/{programme_id}/graph", response_model=ProgrammeDeal)
def update_programme_graph(programme_id: str, graph: GraphSpec) -> ProgrammeDeal:
    try:
        validate_dag_acyclic(graph)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    c = CONTRACT_STORE.update_graph(programme_id, graph)
    if not c:
        raise HTTPException(status_code=404, detail="Programme not found")
    _sync_memory_aliases()
    return contract_to_programme_deal(c)


@app.post("/api/programmes/{programme_id}/bind")
def bind_programme_snapshot(programme_id: str) -> dict:
    res = CONTRACT_STORE.bind_deal(programme_id, LINEAGE)
    if not res:
        raise HTTPException(status_code=404, detail="Programme not found")
    full, manifest_id = res
    _sync_memory_aliases()
    return {"contract": full.model_dump(by_alias=True), "bindManifestId": manifest_id}


@app.get("/api/graph-templates")
def list_graph_templates() -> list[dict]:
    return [
        {
            "templateId": "tpl-occ-xl",
            "category": "Financial Terms",
            "label": "Occ XL (50m xs 80m, 1@100%)",
            "nodeType": "OCC_XL",
            "params": {"occLimit": 50.0, "occAttachment": 80.0, "occFranchise": 0.0, "reinstatements": 1.0, "ripRate": 1.0, "depositPremium": 10.0, "placedShare": 1.0},
        },
        {
            "templateId": "tpl-agg-xl",
            "category": "Financial Terms",
            "label": "Agg XL (35m xs 95m)",
            "nodeType": "AGG_XL",
            "params": {"aggLimit": 35.0, "aggAttachment": 95.0, "perEventFranchise": 10.0, "perEventCap": 60.0, "depositPremium": 7.5, "placedShare": 1.0},
        },
        {
            "templateId": "tpl-qs",
            "category": "Financial Terms",
            "label": "Quota Share (25% / 40m Agg Cap)",
            "nodeType": "AGG_QUOTA_SHARE",
            "params": {"cessionShare": 0.25, "eventCap": 25.0, "aggCap": 40.0, "depositPremium": 8.0, "placedShare": 1.0},
        },
        {
            "templateId": "tpl-surplus",
            "category": "Financial Terms",
            "label": "Surplus Share (4 Lines xs 25m)",
            "nodeType": "SURPLUS_SHARE",
            "params": {"retentionLine": 25.0, "numLines": 4.0, "depositPremium": 6.5, "placedShare": 1.0},
        },
        {
            "templateId": "tpl-cascading",
            "category": "Templates",
            "label": "Cascading Drop-Down (2-Layer)",
            "nodeType": "CASCADING_TOWER",
            "params": {
                "occLimit": 70.0,
                "depositPremium": 14.0,
                "placedShare": 1.0,
                "layers": [
                    {"occLimit": 30.0, "occAttachment": 50.0, "reinstatements": 0.0, "ripRate": 0.0, "depositPremium": 8.0, "placedShare": 1.0},
                    {"occLimit": 40.0, "occAttachment": 80.0, "reinstatements": 0.0, "ripRate": 0.0, "depositPremium": 6.0, "placedShare": 1.0},
                ],
            },
        },
        {
            "templateId": "tpl-flt-rp",
            "category": "Filters",
            "label": "Zone Peril Filter (FL Wind)",
            "nodeType": "REGION_PERIL_FILTER",
            "params": {"regionPerils": ["US-FL-WS"]},
        },
        {
            "templateId": "tpl-flt-2nd",
            "category": "Filters",
            "label": "Event Order Filter (2nd+ Event)",
            "nodeType": "EVENT_ORDER_FILTER",
            "params": {"minOrder": 2, "maxOrder": None, "minLossThreshold": 15.0},
        },
        {
            "templateId": "tpl-combo",
            "category": "Transforms",
            "label": "Combination (+/- Inuring)",
            "nodeType": "COMBINATION",
            "params": {},
        },
        {
            "templateId": "tpl-out-pricing",
            "category": "Outputs",
            "label": "Pricing Output",
            "nodeType": "PRICING_OUTPUT",
            "params": {},
        },
    ]


# ─────────────────────────── Background Industry ILW Engine ───────────────────────────


@app.post("/api/ilw/price", response_model=IlwPriceResponse)
def price_ilw(req: IlwPriceRequest) -> IlwPriceResponse:
    try:
        ind_yelt = SIM_STORE.get_yelt(req.industry_sim_set_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    gross_vec = _compute_bound_gross_loss_vector(req.portfolio_view)
    return price_ilw_with_portfolio_hedge(
        req=req,
        ind_yelt=ind_yelt,
        portfolio_gross_loss_by_trial=gross_vec,
        lineage=LINEAGE,
    )


@app.post("/api/ilw/promote", response_model=ContractFull)
def promote_ilw_to_cms(req: IlwPriceRequest) -> ContractFull:
    full = CONTRACT_STORE.promote_ilw(req, LINEAGE)
    _sync_memory_aliases()
    return full


# ─────────────────────────── Portfolio & One-Click Sliced Rollup ───────────────────────────


@app.get("/api/portfolio/rollup", response_model=PortfolioRollupResponse)
def get_portfolio_rollup(
    view: ViewOfRisk = Query(default=ViewOfRisk.ADJUSTED),
    total_capital: float = Query(default=160.0, alias="totalCapital", gt=0),
) -> PortfolioRollupResponse:
    bound = [
        contract_to_programme_deal(c)
        for c in CONTRACT_STORE.list_contracts(status=ProgrammeStatus.BOUND)
        if c.structure != StructureType.ILW
    ]
    yelt = SIM_STORE.get_yelt(_base_yelt.sim_set_id)
    return evaluate_portfolio_rollup(
        portfolio_id="fund-keystone-j27",
        portfolio_name="Keystone Collateralized Cat Fund — J27",
        programmes=bound,
        yelt=yelt,
        view=view,
        total_capital=total_capital,
        lineage=LINEAGE,
    )


@app.post("/api/portfolio/rollup/slice", response_model=PortfolioRollupResponse)
def rollup_cms_slice(req: SliceRollupRequest) -> PortfolioRollupResponse:
    selected = [c for c in CONTRACT_STORE.select_slice(req) if c.structure != StructureType.ILW]
    if not selected:
        raise HTTPException(status_code=400, detail="No matching non-ILW contracts in slice")
    yelt = SIM_STORE.get_yelt(_base_yelt.sim_set_id)
    return evaluate_portfolio_rollup(
        portfolio_id="slice-rollup",
        portfolio_name=req.portfolio_name,
        programmes=[contract_to_programme_deal(c) for c in selected],
        yelt=yelt,
        view=req.view,
        total_capital=req.total_capital,
        lineage=LINEAGE,
    )


# ─────────────────────────── Deterministic & Lineage ───────────────────────────


@app.post("/api/deterministic/run", response_model=DeterministicRunResponse)
def run_deterministic(req: DeterministicRunRequest) -> DeterministicRunResponse:
    c = CONTRACT_STORE.get_contract(req.programme_id)
    if not c:
        raise HTTPException(status_code=404, detail="Programme not found")
    return evaluate_deterministic_run(req, contract_to_programme_deal(c), LINEAGE)


@app.get("/api/lineage/manifests", response_model=list[RunManifest])
def list_manifests() -> list[RunManifest]:
    return LINEAGE.list_manifests()


@app.get("/api/lineage/manifests/{manifest_id}", response_model=RunManifest)
def get_manifest(manifest_id: str) -> RunManifest:
    m = LINEAGE.get_manifest(manifest_id)
    if not m:
        raise HTTPException(status_code=404, detail="Manifest not found")
    return m


@app.get("/api/lineage/staleness", response_model=list[StaleManifestInfo])
def check_staleness() -> list[StaleManifestInfo]:
    current_hashes = CONTRACT_STORE.current_entity_hashes()
    for s in SIM_STORE.list_sim_sets():
        current_hashes[s.sim_set_id] = s.content_hash
    return LINEAGE.check_staleness(current_hashes)


@app.get("/api/benchmarks")
def get_benchmarks() -> dict:
    bench_file = Path(__file__).resolve().parents[2] / "bench" / "results.json"
    if not bench_file.exists():
        return {"status": "not_run"}
    return json.loads(bench_file.read_text())


app.include_router(
    create_extended_router(
        contract_store=CONTRACT_STORE,
        sim_store=SIM_STORE,
        lineage=LINEAGE,
        base_sim_id=_base_yelt.sim_set_id,
        ind_sim_id=_ind_yelt.sim_set_id,
    )
)
