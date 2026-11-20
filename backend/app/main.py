"""RoaringLion FastAPI entrypoint — Phase 0 First Slice."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.engine.sim_generator import REGION_PERILS
from app.engine.slice_runner import (
    evaluate_deterministic_run,
    evaluate_portfolio_rollup,
    evaluate_programme_dag,
)
from app.models.domain import (
    DagEvalResponse,
    DeterministicRunRequest,
    DeterministicRunResponse,
    PortfolioRollupResponse,
    ProgrammeDeal,
    RunManifest,
    SimSetSummary,
    UpliftRule,
)
from app.models.enums import ProgrammeStatus, ViewOfRisk
from app.services.lineage import LineageRegistry, canonical_hash
from app.services.seed_data import create_phase0_state

app = FastAPI(
    title="RoaringLion Catastrophe Reinsurance Platform",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Phase 0 in-memory state (100k-trial synthetic catalogue + 3 bound programmes + lineage ledger)
SIM_SETS, PROGRAMMES = create_phase0_state(num_trials=100_000)
LINEAGE = LineageRegistry()


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "engine": "roaring_lion_engine (rust-rayon)",
        "simSetsLoaded": len(SIM_SETS),
        "programmesLoaded": len(PROGRAMMES),
    }


@app.get("/api/sim-sets", response_model=list[SimSetSummary])
def list_sim_sets() -> list[SimSetSummary]:
    return [
        SimSetSummary(
            sim_set_id=s.sim_set_id,
            name=s.name,
            event_catalog_id=s.event_catalog_id,
            num_trials=s.num_trials,
            total_events=int(len(s.losses)),
            currency=s.currency,
            region_perils=REGION_PERILS,
            content_hash=s.content_hash,
        )
        for s in SIM_SETS.values()
    ]


@app.get("/api/programmes", response_model=list[ProgrammeDeal])
def list_programmes() -> list[ProgrammeDeal]:
    return list(PROGRAMMES.values())


@app.get("/api/programmes/{programme_id}", response_model=ProgrammeDeal)
def get_programme(programme_id: str) -> ProgrammeDeal:
    prog = PROGRAMMES.get(programme_id)
    if not prog:
        raise HTTPException(status_code=404, detail="Programme not found")
    return prog


@app.put("/api/programmes/{programme_id}/uplifts", response_model=ProgrammeDeal)
def update_programme_uplifts(programme_id: str, rules: list[UpliftRule]) -> ProgrammeDeal:
    prog = PROGRAMMES.get(programme_id)
    if not prog:
        raise HTTPException(status_code=404, detail="Programme not found")
    prog.uplift_set.version += 1
    prog.uplift_set.rules = rules
    prog.uplift_set.content_hash = canonical_hash([r.model_dump() for r in rules])
    return prog


@app.post("/api/programmes/{programme_id}/evaluate", response_model=DagEvalResponse)
def evaluate_programme(programme_id: str) -> DagEvalResponse:
    prog = PROGRAMMES.get(programme_id)
    if not prog:
        raise HTTPException(status_code=404, detail="Programme not found")
    yelt = SIM_SETS[prog.sim_set_id]
    resp, _ = evaluate_programme_dag(prog, yelt, LINEAGE)
    return resp


@app.get("/api/portfolio/rollup", response_model=PortfolioRollupResponse)
def get_portfolio_rollup(
    view: ViewOfRisk = Query(default=ViewOfRisk.ADJUSTED),
    total_capital: float = Query(default=160.0, alias="totalCapital", gt=0),
) -> PortfolioRollupResponse:
    bound = [p for p in PROGRAMMES.values() if p.status == ProgrammeStatus.BOUND]
    yelt = next(iter(SIM_SETS.values()))
    return evaluate_portfolio_rollup(
        portfolio_id="fund-keystone-j27",
        portfolio_name="Keystone Collateralized Cat Fund — J27",
        programmes=bound,
        yelt=yelt,
        view=view,
        total_capital=total_capital,
        lineage=LINEAGE,
    )


@app.post("/api/deterministic/run", response_model=DeterministicRunResponse)
def run_deterministic(req: DeterministicRunRequest) -> DeterministicRunResponse:
    prog = PROGRAMMES.get(req.programme_id)
    if not prog:
        raise HTTPException(status_code=404, detail="Programme not found")
    return evaluate_deterministic_run(req, prog, LINEAGE)


@app.get("/api/lineage/manifests", response_model=list[RunManifest])
def list_manifests() -> list[RunManifest]:
    return LINEAGE.list_manifests()


@app.get("/api/lineage/manifests/{manifest_id}", response_model=RunManifest)
def get_manifest(manifest_id: str) -> RunManifest:
    m = LINEAGE.get_manifest(manifest_id)
    if not m:
        raise HTTPException(status_code=404, detail="Manifest not found")
    return m


@app.get("/api/benchmarks")
def get_benchmarks() -> dict:
    bench_file = Path(__file__).resolve().parents[2] / "bench" / "results.json"
    if not bench_file.exists():
        return {"status": "not_run"}
    return json.loads(bench_file.read_text())
