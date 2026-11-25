"""Extended FastAPI routers for Phases 2b through 9."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.adapters.cat_models import CatModelOrchestrator, ModelRunJob, ModelRunRequest
from app.adapters.exposure_eclipse import BoundDealSyncEvent, ExposureEclipseClient, ExposureProgrammeRef
from app.engine.allocation import RetroAllocationRequest, RetroAllocationResponse, compute_gross_net_retro_allocation
from app.engine.backtest_scenario import (
    BacktestMatrixRequest,
    BacktestMatrixResponse,
    ReverseStressRequest,
    ReverseStressResponse,
    ScenarioCreate,
    ScenarioForgeService,
    ScenarioOut,
    run_all_direction_backtest,
)
from app.engine.burn_cost import BurnCostRequest, BurnCostResponse, evaluate_burning_cost
from app.engine.optimizer import (
    FrontierExplorerResponse,
    FrontierPoint,
    OptimizerLayerCatalogItem,
    OptimizerRequest,
    OptimizerResponse,
    compute_efficient_frontier,
    explore_efficient_frontier,
    list_optimizable_layers,
    solve_portfolio_optimization,
)
from app.engine.pricing_periods_fx import (
    DEFAULT_FX_TABLES,
    FxRateTable,
    SeasonalEarningResponse,
    TechnicalPricingRequest,
    TechnicalPricingResponse,
    compare_model_versions,
    compute_seasonal_earning,
    compute_technical_pricing,
    convert_currency,
)
from app.engine.slice_runner import evaluate_portfolio_rollup, evaluate_programme_dag
from app.engine.synthetic import SyntheticLossRequest, generate_synthetic_freq_sev
from app.models.domain import DeterministicRunResponse
from app.models.enums import ProgrammeStatus, StructureType, ViewOfRisk
from app.models.phase1 import ContractNoteCreate, ContractNoteOut, SimSetDetail
from app.services.branching import BranchCreate, BranchDiffResponse, BranchOut, BranchService
from app.services.claims import ClaimCreate, ClaimOut, ClaimRevision, ClaimsService
from app.services.contracts import ContractStore, contract_to_programme_deal
from app.services.exports import build_portfolio_csv, build_portfolio_tearsheet_html, build_programme_tearsheet_html
from app.services.fund_ops import CashEntry, EventReserve, FundOpsStore, InvestorSeries, MarketMark, SlipIntakeDraft, check_permission
from app.services.lineage import LineageRegistry
from app.services.sim_sets import SimSetStore


class SlipTextPayload(BaseModel):
    rawText: str


class FxConvertPayload(BaseModel):
    amount: float = Field(ge=0, le=1e15, allow_inf_nan=False)
    fromCurrency: str
    toCurrency: str
    profileId: str = "FX-2027-PLAN"


class ModelChangePayload(BaseModel):
    programmeId: str
    oldSimSetId: str
    newSimSetId: str
    view: ViewOfRisk = ViewOfRisk.ADJUSTED


def create_extended_router(
    *,
    contract_store: ContractStore,
    sim_store: SimSetStore,
    lineage: LineageRegistry,
    base_sim_id: str,
    ind_sim_id: str,
) -> APIRouter:
    router = APIRouter()
    claims_svc = ClaimsService()
    branch_svc = BranchService()
    forge_svc = ScenarioForgeService()
    cat_orch = CatModelOrchestrator()
    ee_client = ExposureEclipseClient()
    ops_store = FundOpsStore()

    def _bound_deals():
        return [
            contract_to_programme_deal(c)
            for c in contract_store.list_contracts(status=ProgrammeStatus.BOUND)
            if c.structure != StructureType.ILW
        ]

    def _all_non_ilw_deals():
        return [
            contract_to_programme_deal(c)
            for c in contract_store.list_contracts()
            if c.structure != StructureType.ILW
        ]

    # ── Phase 2b: Burn Cost & Live Claims ──

    @router.post("/api/burn-cost/evaluate", response_model=BurnCostResponse)
    def run_burn_cost(req: BurnCostRequest) -> BurnCostResponse:
        c = contract_store.get_contract(req.programme_id)
        if not c:
            raise HTTPException(status_code=404, detail="Programme not found")
        prog = contract_to_programme_deal(c)
        yelt = sim_store.get_yelt(prog.sim_set_id)
        dag_res, _ = evaluate_programme_dag(prog, yelt, LineageRegistry(persist=False), views=[ViewOfRisk.ADJUSTED])
        mod_el = sum(
            m.el for m in dag_res.views["ADJUSTED"]
            if m.node_type.value in ("OCC_XL", "AGG_XL", "QUOTA_SHARE", "AGG_QUOTA_SHARE", "SURPLUS_SHARE", "CASCADING_TOWER")
        )
        return evaluate_burning_cost(req, prog, lineage, modelled_el_override=mod_el)

    @router.get("/api/claims", response_model=list[ClaimOut])
    def list_claims(programme_id: str | None = Query(default=None, alias="programmeId")) -> list[ClaimOut]:
        return claims_svc.list_claims(programme_id=programme_id)

    @router.post("/api/claims", response_model=ClaimOut)
    def create_claim(payload: ClaimCreate) -> ClaimOut:
        check_permission("underwriter", "create", "claim")
        if not contract_store.get_contract(payload.programme_id):
            raise HTTPException(status_code=404, detail="Programme not found")
        existing = claims_svc.list_claims(programme_id=payload.programme_id)
        if any(c.claim_no == payload.claim_no for c in existing):
            raise HTTPException(status_code=409, detail=f"Claim {payload.claim_no} already exists for this programme; revise it instead")
        return claims_svc.create_claim(payload)

    @router.post("/api/claims/{claim_id}/revise", response_model=ClaimOut)
    def revise_claim(claim_id: str, rev: ClaimRevision) -> ClaimOut:
        out = claims_svc.revise_claim(claim_id, rev)
        if not out:
            raise HTTPException(status_code=404, detail="Claim not found")
        return out

    @router.post("/api/claims/evaluate/{programme_id}", response_model=DeterministicRunResponse)
    def evaluate_programme_claims(programme_id: str, hours_clause: int = Query(default=72, ge=0, le=8760)) -> DeterministicRunResponse:
        c = contract_store.get_contract(programme_id)
        if not c:
            raise HTTPException(status_code=404, detail="Programme not found")
        return claims_svc.evaluate_claims_for_programme(contract_to_programme_deal(c), lineage, hours_clause=hours_clause)

    # ── Phase 3: Gross vs Net Retro Cost & Benefit Allocation + Waterfall ──

    @router.post("/api/allocation/gross-net", response_model=RetroAllocationResponse)
    def run_gross_net_allocation(req: RetroAllocationRequest) -> RetroAllocationResponse:
        return compute_gross_net_retro_allocation(
            req=req,
            programmes=_bound_deals(),
            yelt=sim_store.get_yelt(base_sim_id),
            ind_yelt=sim_store.get_yelt(ind_sim_id),
            lineage=lineage,
        )

    # ── Phase 3b: Portfolio Branching, All-Direction Backtest Matrix & Scenario Forge ──

    @router.get("/api/branches", response_model=list[BranchOut])
    def list_branches() -> list[BranchOut]:
        return branch_svc.list_branches()

    @router.post("/api/branches", response_model=BranchOut)
    def create_branch(payload: BranchCreate) -> BranchOut:
        known = {c.programme_id for c in contract_store.list_contracts()}
        unknown = (set(payload.signed_line_overrides) | set(payload.excluded_programme_ids)) - known
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown programme(s) in branch: {sorted(unknown)}")
        return branch_svc.create_branch(payload, lineage)

    @router.post("/api/branches/{branch_id}/diff", response_model=BranchDiffResponse)
    def diff_branch(
        branch_id: str,
        view: ViewOfRisk = Query(default=ViewOfRisk.ADJUSTED),
        total_capital: float = Query(default=160.0, alias="totalCapital", gt=0),
    ) -> BranchDiffResponse:
        br = branch_svc.get_branch(branch_id)
        if not br:
            raise HTTPException(status_code=404, detail="Branch not found")
        return branch_svc.diff_branch(
            branch=br,
            baseline_programmes=_bound_deals(),
            yelt=sim_store.get_yelt(base_sim_id),
            view=view,
            total_capital=total_capital,
            lineage=lineage,
        )

    @router.post("/api/backtest/matrix", response_model=BacktestMatrixResponse)
    def run_backtest_matrix(req: BacktestMatrixRequest) -> BacktestMatrixResponse:
        br = branch_svc.get_branch(req.branch_id) if req.branch_id else None
        if req.branch_id and br is None:
            raise HTTPException(status_code=404, detail="Branch not found")
        return run_all_direction_backtest(
            req=req,
            baseline_programmes=_bound_deals(),
            branch=br,
            lineage=lineage,
        )

    @router.get("/api/scenarios", response_model=list[ScenarioOut])
    def list_scenarios() -> list[ScenarioOut]:
        return forge_svc.list_scenarios()

    @router.post("/api/scenarios", response_model=ScenarioOut)
    def create_scenario(payload: ScenarioCreate) -> ScenarioOut:
        return forge_svc.create_scenario(payload)

    @router.post("/api/scenarios/reverse-stress", response_model=ReverseStressResponse)
    def run_reverse_stress(req: ReverseStressRequest) -> ReverseStressResponse:
        return forge_svc.reverse_stress(req, _bound_deals(), lineage)

    # ── Phase 4: True HiGHS Constrained Portfolio Optimizer & Efficient Frontier ──

    @router.post("/api/optimizer/solve", response_model=OptimizerResponse)
    def run_optimizer(req: OptimizerRequest) -> OptimizerResponse:
        return solve_portfolio_optimization(
            req=req,
            programmes=_all_non_ilw_deals(),
            yelt=sim_store.get_yelt(base_sim_id),
            ind_yelt=sim_store.get_yelt(ind_sim_id),
            lineage=lineage,
        )

    @router.post("/api/optimizer/frontier", response_model=list[FrontierPoint])
    def run_frontier(req: OptimizerRequest, num_points: int = Query(default=6, ge=2, le=12)) -> list[FrontierPoint]:
        return compute_efficient_frontier(
            req=req,
            programmes=_all_non_ilw_deals(),
            yelt=sim_store.get_yelt(base_sim_id),
            ind_yelt=sim_store.get_yelt(ind_sim_id),
            lineage=lineage,
            num_points=num_points,
        )

    @router.get("/api/optimizer/layers", response_model=list[OptimizerLayerCatalogItem])
    def get_optimizer_layers(
        view: ViewOfRisk = Query(default=ViewOfRisk.ADJUSTED),
        mode: str = Query(default="FORWARD_PLANNING"),
    ) -> list[OptimizerLayerCatalogItem]:
        safe_mode = "CURRENT" if mode == "CURRENT" else "FORWARD_PLANNING"
        return list_optimizable_layers(
            programmes=_all_non_ilw_deals(),
            yelt=sim_store.get_yelt(base_sim_id),
            ind_yelt=sim_store.get_yelt(ind_sim_id),
            view=view,
            mode=safe_mode,
        )

    @router.post("/api/optimizer/explore", response_model=FrontierExplorerResponse)
    def run_frontier_explorer(
        req: OptimizerRequest,
        num_points: int = Query(default=12, ge=4, le=20),
    ) -> FrontierExplorerResponse:
        return explore_efficient_frontier(
            req=req,
            programmes=_all_non_ilw_deals(),
            yelt=sim_store.get_yelt(base_sim_id),
            ind_yelt=sim_store.get_yelt(ind_sim_id),
            lineage=lineage,
            num_points=num_points,
        )

    # ── Phase 5: Cat Model API Orchestrator & Synthetic Freq-Sev Generator ──

    @router.post("/api/model-runs", response_model=ModelRunJob)
    def trigger_model_run(req: ModelRunRequest) -> ModelRunJob:
        if not contract_store.get_contract(req.programme_id):
            raise HTTPException(status_code=404, detail="Programme not found")
        try:
            return cat_orch.submit_run(req, sim_store, lineage)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/model-runs", response_model=list[ModelRunJob])
    def list_model_runs() -> list[ModelRunJob]:
        return cat_orch.list_jobs()

    @router.post("/api/synthetic/generate", response_model=SimSetDetail)
    def generate_synthetic(req: SyntheticLossRequest) -> SimSetDetail:
        return generate_synthetic_freq_sev(req, sim_store, lineage)

    # ── Phase 6: Bi-Directional ExposureEclipse Integration ──

    @router.get("/api/integrations/exposure-eclipse/programmes", response_model=list[ExposureProgrammeRef])
    def list_ee_programmes() -> list[ExposureProgrammeRef]:
        return ee_client.list_exposure_programmes()

    @router.post("/api/integrations/exposure-eclipse/sync-bound/{programme_id}", response_model=BoundDealSyncEvent)
    def sync_bound_to_ee(programme_id: str) -> BoundDealSyncEvent:
        c = contract_store.get_contract(programme_id)
        if not c:
            raise HTTPException(status_code=404, detail="Contract not found")
        return ee_client.record_bound_sync(
            programme_id=c.programme_id,
            ee_programme_id=c.exposure_eclipse_programme_id,
            status=c.status.value,
            signed_line=c.signed_line,
            limit_m=c.limit,
            rol=c.rol,
        )

    # ── Phase 7: Technical Pricing, Seasonal Earning, FX Tables & Model Change ──

    @router.post("/api/pricing/technical", response_model=TechnicalPricingResponse)
    def technical_pricing(req: TechnicalPricingRequest) -> TechnicalPricingResponse:
        return compute_technical_pricing(req)

    @router.get("/api/periods/seasonal", response_model=SeasonalEarningResponse)
    def seasonal_earning(
        region_peril: str = Query(default="US-FL-WS", alias="regionPeril"),
        as_of_day: int = Query(default=270, alias="asOfDay", ge=1, le=365),
    ) -> SeasonalEarningResponse:
        try:
            return compute_seasonal_earning(sim_store.get_yelt(base_sim_id), region_peril, as_of_day)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/api/fx/tables", response_model=list[FxRateTable])
    def list_fx_tables() -> list[FxRateTable]:
        return list(DEFAULT_FX_TABLES.values())

    @router.post("/api/fx/convert")
    def fx_convert(payload: FxConvertPayload) -> dict:
        try:
            return convert_currency(payload.amount, payload.fromCurrency, payload.toCurrency, payload.profileId)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/api/model-change/compare")
    def model_change_compare(payload: ModelChangePayload) -> dict:
        c = contract_store.get_contract(payload.programmeId)
        if not c:
            raise HTTPException(status_code=404, detail="Programme not found")
        try:
            old_yelt = sim_store.get_yelt(payload.oldSimSetId)
            new_yelt = sim_store.get_yelt(payload.newSimSetId)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return compare_model_versions(
            contract_to_programme_deal(c),
            old_yelt,
            new_yelt,
            view=payload.view,
        )

    # ── Phases 8 & 9: Fund Ops (Investors, Reserves, Cash, Market Marks), Slip Intake & Alerts ──

    @router.get("/api/fund-ops/investors", response_model=list[InvestorSeries])
    def list_investors() -> list[InvestorSeries]:
        return ops_store.investors

    @router.get("/api/fund-ops/reserves", response_model=list[EventReserve])
    def list_reserves() -> list[EventReserve]:
        return ops_store.reserves

    @router.get("/api/fund-ops/cash", response_model=list[CashEntry])
    def list_cash() -> list[CashEntry]:
        return ops_store.cash_entries

    @router.get("/api/market-marks", response_model=list[MarketMark])
    def list_market_marks() -> list[MarketMark]:
        return ops_store.market_marks

    @router.post("/api/intake/parse-slip", response_model=SlipIntakeDraft)
    def parse_slip(payload: SlipTextPayload) -> SlipIntakeDraft:
        if not payload.rawText.strip():
            raise HTTPException(status_code=422, detail="Slip text is empty")
        return ops_store.parse_slip_text(payload.rawText)

    @router.get("/api/alerts")
    def list_alerts() -> list[dict]:
        stale = lineage.check_staleness(contract_store.current_entity_hashes())
        stale_count = sum(1 for s in stale if s.is_stale)
        alerts = []
        if stale_count > 0:
            alerts.append({"severity": "WARN", "code": "STALE_MANIFESTS", "message": f"{stale_count} historic run manifests are STALE after upstream edits."})
        alerts.append({"severity": "INFO", "code": "ACCESS_HOOK_ACTIVE", "message": "Permission/approval hook active (Phase 9 enforcement ready)."})
        return alerts

    # ── Phase 6 / 9: Excel CSV & Printable PDF Tear-Sheet Exports (Screen = Export via same runners) ──

    @router.get("/api/exports/portfolio-csv")
    def export_portfolio_csv(
        view: ViewOfRisk = Query(default=ViewOfRisk.ADJUSTED),
        total_capital: float = Query(default=160.0, alias="totalCapital", gt=0),
    ) -> PlainTextResponse:
        rollup = evaluate_portfolio_rollup(
            portfolio_id="fund-keystone-j27",
            portfolio_name="Keystone Collateralized Cat Fund — J27",
            programmes=_bound_deals(),
            yelt=sim_store.get_yelt(base_sim_id),
            view=view,
            total_capital=total_capital,
            lineage=lineage,
        )
        csv_body = build_portfolio_csv(rollup)
        return PlainTextResponse(
            content=csv_body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="roaringlion-portfolio-{view.value.lower()}.csv"'},
        )

    @router.get("/api/exports/portfolio-tearsheet", response_class=HTMLResponse)
    def export_portfolio_tearsheet(
        view: ViewOfRisk = Query(default=ViewOfRisk.ADJUSTED),
        total_capital: float = Query(default=160.0, alias="totalCapital", gt=0),
    ) -> HTMLResponse:
        rollup = evaluate_portfolio_rollup(
            portfolio_id="fund-keystone-j27",
            portfolio_name="Keystone Collateralized Cat Fund — J27",
            programmes=_bound_deals(),
            yelt=sim_store.get_yelt(base_sim_id),
            view=view,
            total_capital=total_capital,
            lineage=lineage,
        )
        return HTMLResponse(content=build_portfolio_tearsheet_html(rollup))

    @router.get("/api/exports/programme-tearsheet/{programme_id}", response_class=HTMLResponse)
    def export_programme_tearsheet(programme_id: str) -> HTMLResponse:
        c = contract_store.get_contract(programme_id)
        if not c:
            raise HTTPException(status_code=404, detail="Programme not found")
        prog = contract_to_programme_deal(c)
        dag_eval, _ = evaluate_programme_dag(
            prog,
            sim_store.get_yelt(prog.sim_set_id),
            lineage,
            industry_yelt=sim_store.get_yelt(ind_sim_id),
        )
        return HTMLResponse(content=build_programme_tearsheet_html(prog, dag_eval))

    # ── CMS Programme Front Sheet, Underwriting Notes & 360° Linkage Dossier ──

    @router.get("/api/contracts/{programme_id}/notes", response_model=list[ContractNoteOut])
    def list_contract_notes(
        programme_id: str,
        scope: str = Query(default="programme"),
    ) -> list[ContractNoteOut]:
        if not contract_store.get_contract(programme_id):
            raise HTTPException(status_code=404, detail="Contract not found")
        return contract_store.list_notes(programme_id, scope="cedent" if scope == "cedent" else "programme")

    @router.post("/api/contracts/{programme_id}/notes", response_model=ContractNoteOut)
    def create_contract_note(programme_id: str, payload: ContractNoteCreate) -> ContractNoteOut:
        if not payload.body.strip():
            raise HTTPException(status_code=422, detail="Note body cannot be empty")
        out = contract_store.add_note(programme_id, payload)
        if not out:
            raise HTTPException(status_code=404, detail="Contract not found")
        return out

    @router.get("/api/contracts/{programme_id}/dossier")
    def get_contract_dossier(programme_id: str) -> dict:
        c = contract_store.get_contract(programme_id)
        if not c:
            raise HTTPException(status_code=404, detail="Contract not found")

        prog = contract_to_programme_deal(c)
        yelt = sim_store.get_yelt(prog.sim_set_id)
        dag_eval, _ = evaluate_programme_dag(
            prog,
            yelt,
            LineageRegistry(persist=False),
            industry_yelt=sim_store.get_yelt(ind_sim_id),
        )

        # Related programmes for same cedent
        related = [
            {
                "programmeId": other.programme_id,
                "frontSheetCode": other.front_sheet.front_sheet_code,
                "programmeName": other.programme_name,
                "treatyYear": other.treaty_year,
                "renewalPeriod": other.renewal_period,
                "status": other.status.value,
                "limit": other.limit,
                "rol": other.rol,
            }
            for other in contract_store.list_contracts()
            if other.cedent_name == c.cedent_name and other.programme_id != c.programme_id
        ]

        # Linked ExposureEclipse programme
        ee_progs = ee_client.list_exposure_programmes()
        ee_match = next(
            (
                ep.model_dump(by_alias=True)
                for ep in ee_progs
                if (c.exposure_eclipse_programme_id and ep.programme_id == c.exposure_eclipse_programme_id)
                or ep.cedent_name.lower() == c.cedent_name.lower()
            ),
            None,
        )

        # Linked SimSet + Cat Model API jobs
        sim_detail = next((s.model_dump(by_alias=True) for s in sim_store.list_sim_sets() if s.sim_set_id == c.sim_set_id), None)
        model_jobs = [j.model_dump(by_alias=True) for j in cat_orch.list_jobs() if j.programme_id == programme_id]

        # Linked Claims + Recent Manifests
        claims = claims_svc.list_claims(programme_id=programme_id)
        stale_map = {s.manifest_id: s.is_stale for s in lineage.check_staleness(contract_store.current_entity_hashes())}
        manifests = [
            {
                "manifestId": m.manifest_id,
                "runType": m.run_type,
                "createdAt": m.created_at.isoformat(),
                "outputHash": m.output_hash,
                "isStale": stale_map.get(m.manifest_id, False),
            }
            for m in lineage.list_manifests(limit=40)
            if m.parameters.get("programme_id") == programme_id
            or any(b.entity_id == programme_id for b in m.breadcrumb)
        ][:10]

        return {
            "contract": c.model_dump(by_alias=True),
            "relatedProgrammes": related,
            "notes": [n.model_dump(by_alias=True) for n in contract_store.list_notes(programme_id, scope="cedent")],
            "pricingLink": {
                "graphId": dag_eval.graph_id,
                "graphVersion": dag_eval.graph_version,
                "computeMs": dag_eval.compute_ms,
                "unadjustedNodes": [m.model_dump(by_alias=True) for m in dag_eval.views.get("UNADJUSTED", [])],
                "adjustedNodes": [m.model_dump(by_alias=True) for m in dag_eval.views.get("ADJUSTED", [])],
            },
            "exposureLink": ee_match,
            "modelingLink": {
                "simSet": sim_detail,
                "upliftSet": c.uplift_set.model_dump(by_alias=True),
                "modelRuns": model_jobs,
            },
            "claimsCount": len(claims),
            "recentManifests": manifests,
        }

    return router
