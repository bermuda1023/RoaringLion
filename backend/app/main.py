"""RoaringLion FastAPI entrypoint — Phase 0 + Phase 1 (Persistent CMS, Parquet SimSets/ELT, ILW Library, Breadcrumbs)."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, Response, UploadFile
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
    BreadcrumbStep,
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
    ContractLayer,
    ContractLayersUpdateRequest,
    ContractUpdate,
    EltIngestRequest,
    IlwPriceRequest,
    IlwPriceResponse,
    SimSetDetail,
    SliceRollupRequest,
    StaleManifestInfo,
    YeltFromNodeRequest,
)
from app.services.contracts import ContractStore, contract_to_programme_deal, graph_layers
from app.services.lineage import LineageRegistry
from app.services.node_outputs import node_yelt_table, resolve_pricing_links, yelt_export_bytes, yelt_summary
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
    if len(raw) > 200_000_000:
        raise HTTPException(status_code=413, detail="Upload exceeds 200 MB limit")
    if not 1 <= num_trials <= 1_000_000:
        raise HTTPException(status_code=422, detail="numTrials must be between 1 and 1,000,000")
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
    try:
        SIM_STORE.get_yelt(payload.sim_set_id)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown simSetId: {payload.sim_set_id}") from exc
    full = CONTRACT_STORE.create_contract(payload, LINEAGE)
    _sync_memory_aliases()
    return full


@app.put("/api/contracts/{programme_id}", response_model=ContractFull)
def update_contract(programme_id: str, patch: ContractUpdate) -> ContractFull:
    if patch.sim_set_id is not None:
        try:
            SIM_STORE.get_yelt(patch.sim_set_id)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"Unknown simSetId: {patch.sim_set_id}") from exc
    full = CONTRACT_STORE.update_contract(programme_id, patch)
    if not full:
        raise HTTPException(status_code=404, detail="Contract not found")
    _sync_memory_aliases()
    return full


@app.get("/api/contracts/{programme_id}/layers", response_model=list[ContractLayer])
def get_contract_layers(programme_id: str) -> list[ContractLayer]:
    c = CONTRACT_STORE.get_contract(programme_id)
    if not c:
        raise HTTPException(status_code=404, detail="Contract not found")
    return graph_layers(c.graph, c.written_line, c.signed_line)


@app.put("/api/contracts/{programme_id}/layers", response_model=list[ContractLayer])
def update_contract_layers(programme_id: str, payload: ContractLayersUpdateRequest) -> list[ContractLayer]:
    try:
        c = CONTRACT_STORE.update_layers(programme_id, payload.layers)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not c:
        raise HTTPException(status_code=404, detail="Contract not found")
    _sync_memory_aliases()
    return graph_layers(c.graph, c.written_line, c.signed_line)


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
    resp, _, _ = _evaluate_contract(c, LINEAGE)
    return resp


def _evaluate_contract(c: ContractFull, lineage: LineageRegistry, views: list[ViewOfRisk] | None = None):
    prog = contract_to_programme_deal(c)
    yelt = SIM_STORE.get_yelt(prog.sim_set_id)
    ind_yelt = SIM_STORE.get_yelt(_ind_yelt.sim_set_id)
    links = resolve_pricing_links(prog.graph, c, CONTRACT_STORE.get_contract)
    try:
        resp, vecs = evaluate_programme_dag(prog, yelt, lineage, views=views, industry_yelt=ind_yelt, pricing_links=links)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return resp, vecs, yelt


def _node_yelt(programme_id: str, node_id: str, view: ViewOfRisk, *, sim_set_id: str = "", name: str = ""):
    c = CONTRACT_STORE.get_contract(programme_id)
    if not c:
        raise HTTPException(status_code=404, detail="Programme not found")
    node = next((n for n in c.graph.nodes if n.node_id == node_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found on the saved graph; save the graph first")
    _, vecs, yelt = _evaluate_contract(c, LineageRegistry(persist=False), views=[view])
    table = node_yelt_table(
        yelt,
        vecs["events"][view.value][node_id],
        sim_set_id=sim_set_id or f"node-{node_id}",
        name=name or f"{c.cedent_name} — {node.label}",
        lineage_key={"graph": c.graph.content_hash, "node": node_id, "view": view.value, "sim_set": yelt.content_hash},
    )
    return c, node, yelt, table


def _yelt_view(params: dict, override: ViewOfRisk | None) -> ViewOfRisk:
    if override is not None:
        return override
    try:
        return ViewOfRisk(str(params.get("view", ViewOfRisk.ADJUSTED.value)))
    except ValueError:
        return ViewOfRisk.ADJUSTED


@app.get("/api/programmes/{programme_id}/nodes/{node_id}/yelt")
def node_yelt_summary(programme_id: str, node_id: str, view: ViewOfRisk | None = None) -> dict:
    c = CONTRACT_STORE.get_contract(programme_id)
    node = next((n for n in c.graph.nodes if n.node_id == node_id), None) if c else None
    v = _yelt_view(node.params if node else {}, view)
    c, node, _, table = _node_yelt(programme_id, node_id, v)
    feeds = [e.source for e in c.graph.edges if e.target == node_id]
    labels = {n.node_id: n.label for n in c.graph.nodes}
    return {
        "programmeId": programme_id,
        "nodeId": node_id,
        "nodeLabel": node.label,
        "view": v.value,
        "inputs": [{"nodeId": f, "label": labels.get(f, f)} for f in feeds],
        "graphVersion": c.graph.version,
        **yelt_summary(table),
    }


@app.get("/api/programmes/{programme_id}/nodes/{node_id}/yelt/export")
def node_yelt_export(
    programme_id: str,
    node_id: str,
    view: ViewOfRisk | None = None,
    fmt: str = Query(default="csv", alias="format", pattern="^(csv|parquet)$"),
) -> Response:
    c = CONTRACT_STORE.get_contract(programme_id)
    node = next((n for n in c.graph.nodes if n.node_id == node_id), None) if c else None
    v = _yelt_view(node.params if node else {}, view)
    c, node, _, table = _node_yelt(programme_id, node_id, v)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", f"{c.profile.unique_code or c.programme_id}_{node.label}_{v.value}").strip("_")
    return Response(
        content=yelt_export_bytes(table, fmt),
        media_type="application/vnd.apache.parquet" if fmt == "parquet" else "text/csv",
        headers={"Content-Disposition": f'attachment; filename="{stem if "YELT" in stem else stem + "_YELT"}.{fmt}"'},
    )


@app.post("/api/programmes/{programme_id}/nodes/{node_id}/yelt/save", response_model=SimSetDetail)
def node_yelt_save(programme_id: str, node_id: str, req: YeltFromNodeRequest) -> SimSetDetail:
    c = CONTRACT_STORE.get_contract(programme_id)
    node = next((n for n in c.graph.nodes if n.node_id == node_id), None) if c else None
    v = _yelt_view(node.params if node else {}, req.view)
    sim_id = f"sim-node-{uuid.uuid4().hex[:10]}"
    c, node, yelt, table = _node_yelt(programme_id, node_id, v, sim_set_id=sim_id, name=(req.name or "").strip())
    if not len(table.losses):
        raise HTTPException(status_code=422, detail="This node produces no losses, so there is no YELT to save")
    detail = SIM_STORE.register_derived(
        table,
        LINEAGE,
        breadcrumb=[
            BreadcrumbStep(entity_type="SIM_SET", entity_id=yelt.sim_set_id, label=yelt.name, content_hash=yelt.content_hash),
            BreadcrumbStep(entity_type="PROGRAMME", entity_id=c.programme_id, label=f"{c.cedent_name} — {c.programme_name}"),
            BreadcrumbStep(
                entity_type="DAG_GRAPH",
                entity_id=c.graph.graph_id,
                label=f"{c.graph.name} · {node.label}",
                version=f"v{c.graph.version}",
                content_hash=c.graph.content_hash,
            ),
        ],
        parameters={"programme_id": programme_id, "node_id": node_id, "view": v.value},
    )
    SIM_SETS[detail.sim_set_id] = table
    return detail


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
def list_graph_templates(programme_id: str | None = Query(default=None, alias="programmeId")) -> list[dict]:
    """Return blank-canvas node types plus optional CMS-suggested layers for the active programme."""
    templates: list[dict] = []

    if programme_id:
        c = CONTRACT_STORE.get_contract(programme_id)
        if c:
            struct_node = {
                StructureType.OCC_XL: "OCC_XL",
                StructureType.AGG_XL: "AGG_XL",
                StructureType.QUOTA_SHARE: "AGG_QUOTA_SHARE",
                StructureType.CASCADING: "CASCADING_TOWER",
                StructureType.ILW: "ILW_OPTION",
            }.get(c.structure, "OCC_XL")

            if struct_node == "AGG_XL":
                cms_params: dict = {
                    "aggLimit": c.limit,
                    "aggAttachment": c.attachment,
                    "perEventFranchise": 0.0,
                    "perEventCap": None,
                    "depositPremium": c.deposit_premium,
                    "placedShare": c.signed_line,
                }
            elif struct_node == "AGG_QUOTA_SHARE":
                cms_params = {
                    "cessionShare": c.signed_line,
                    "eventCap": c.limit,
                    "aggCap": c.limit,
                    "depositPremium": c.deposit_premium,
                    "placedShare": 1.0,
                }
            else:
                cms_params = {
                    "occLimit": c.limit,
                    "occAttachment": c.attachment,
                    "occFranchise": 0.0,
                    "reinstatements": 1.0,
                    "ripRate": 1.0,
                    "depositPremium": c.deposit_premium,
                    "placedShare": c.signed_line,
                }

            templates.append({
                "templateId": f"cms-layer-{c.programme_id}",
                "category": "Suggested from CMS",
                "source": "CMS",
                "label": f"{c.programme_name} ({c.limit:g}m xs {c.attachment:g}m)",
                "nodeType": struct_node,
                "params": cms_params,
            })

            if c.region_perils:
                templates.append({
                    "templateId": f"cms-perils-{c.programme_id}",
                    "category": "Suggested from CMS",
                    "source": "CMS",
                    "label": f"Programme Perils ({', '.join(c.region_perils)})",
                    "nodeType": "REGION_PERIL_FILTER",
                    "params": {"regionPerils": list(c.region_perils)},
                })

            if c.prior_contract_id:
                prior = CONTRACT_STORE.get_contract(c.prior_contract_id)
                if prior:
                    for n in prior.graph.nodes:
                        if n.node_type.value in ("OCC_XL", "AGG_XL", "QUOTA_SHARE", "AGG_QUOTA_SHARE", "SURPLUS_SHARE", "CASCADING_TOWER"):
                            templates.append({
                                "templateId": f"cms-prior-{prior.programme_id}-{n.node_id}",
                                "category": f"Prior UY ({prior.treaty_year}) CMS",
                                "source": "CMS",
                                "label": n.label,
                                "nodeType": n.node_type.value,
                                "params": dict(n.params),
                            })

    # Clean blank-canvas node palette (no hardcoded layer sizes or preset terms)
    templates.extend([
        {
            "templateId": "blank-occ-xl",
            "category": "Financial Terms",
            "source": "BLANK",
            "label": "Occurrence XL",
            "nodeType": "OCC_XL",
            "params": {
                "occLimit": "",
                "occAttachment": "",
                "occFranchise": 0.0,
                "reinstatements": 0.0,
                "ripRate": 0.0,
                "depositPremium": "",
                "placedShare": 1.0,
            },
        },
        {
            "templateId": "blank-agg-xl",
            "category": "Financial Terms",
            "source": "BLANK",
            "label": "Aggregate XL",
            "nodeType": "AGG_XL",
            "params": {
                "aggLimit": "",
                "aggAttachment": "",
                "perEventFranchise": 0.0,
                "perEventCap": "",
                "depositPremium": "",
                "placedShare": 1.0,
            },
        },
        {
            "templateId": "blank-qs",
            "category": "Financial Terms",
            "source": "BLANK",
            "label": "Quota Share",
            "nodeType": "AGG_QUOTA_SHARE",
            "params": {
                "cessionShare": "",
                "eventCap": "",
                "aggCap": "",
                "depositPremium": "",
                "placedShare": 1.0,
            },
        },
        {
            "templateId": "blank-surplus",
            "category": "Financial Terms",
            "source": "BLANK",
            "label": "Surplus Share",
            "nodeType": "SURPLUS_SHARE",
            "params": {
                "retentionLine": "",
                "numLines": "",
                "depositPremium": "",
                "placedShare": 1.0,
            },
        },
        {
            "templateId": "blank-cascading",
            "category": "Financial Terms",
            "source": "BLANK",
            "label": "Cascading Tower",
            "nodeType": "CASCADING_TOWER",
            "params": {
                "occLimit": "",
                "depositPremium": "",
                "placedShare": 1.0,
                "layers": [],
            },
        },
        {
            "templateId": "blank-flt-rp",
            "category": "Filters",
            "source": "BLANK",
            "label": "Zone / Peril Filter",
            "nodeType": "REGION_PERIL_FILTER",
            "params": {"zones": [], "perils": [], "regionPerils": []},
        },
        {
            "templateId": "blank-flt-order",
            "category": "Filters",
            "source": "BLANK",
            "label": "Event Order Filter",
            "nodeType": "EVENT_ORDER_FILTER",
            "params": {"minOrder": 2, "maxOrder": "", "minLossThreshold": 0.0},
        },
        {
            "templateId": "blank-combo",
            "category": "Transforms",
            "source": "BLANK",
            "label": "Combination (+/- Inuring)",
            "nodeType": "COMBINATION",
            "params": {},
        },
        {
            "templateId": "blank-out-pricing",
            "category": "Outputs",
            "source": "BLANK",
            "label": "Pricing Output",
            "nodeType": "PRICING_OUTPUT",
            "params": {},
        },
        {
            "templateId": "blank-out-yelt",
            "category": "Outputs",
            "source": "BLANK",
            "label": "YELT Output",
            "nodeType": "YELT_OUTPUT",
            "params": {"view": "ADJUSTED"},
        },
    ])
    return templates


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
        portfolio_id="fund-main-j27",
        portfolio_name="RoaringLion Collateralized Cat Fund — J27",
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
    try:
        return evaluate_deterministic_run(req, contract_to_programme_deal(c), LINEAGE)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
