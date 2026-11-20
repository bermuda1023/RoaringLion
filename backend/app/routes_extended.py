"""Extended FastAPI routers for Phases 2b through 9."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

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
    FrontierPoint,
    OptimizerRequest,
    OptimizerResponse,
    compute_efficient_frontier,
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
from app.engine.slice_runner import evaluate_programme_dag
from app.engine.synthetic import SyntheticLossRequest, generate_synthetic_freq_sev
from app.models.domain import DeterministicRunResponse
from app.models.enums import ProgrammeStatus, StructureType, ViewOfRisk
from app.models.phase1 import SimSetDetail
from app.services.branching import BranchCreate, BranchDiffResponse, BranchOut, BranchService
from app.services.claims import ClaimCreate, ClaimOut, ClaimRevision, ClaimsService
from app.services.contracts import ContractStore, contract_to_programme_deal
from app.services.fund_ops import CashEntry, EventReserve, FundOpsStore, InvestorSeries, MarketMark, SlipIntakeDraft, check_permission
from app.services.lineage import LineageRegistry
from app.services.sim_sets import SimSetStore


class SlipTextPayload(BaseModel):
    rawText: str


class FxConvertPayload(BaseModel):
    amount: float
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
        return claims_svc.create_claim(payload)

    @router.post("/api/claims/{claim_id}/revise", response_model=ClaimOut)
    def revise_claim(claim_id: str, rev: ClaimRevision) -> ClaimOut:
        out = claims_svc.revise_claim(claim_id, rev)
        if not out:
            raise HTTPException(status_code=404, detail="Claim not found")
        return out

    @router.post("/api/claims/evaluate/{programme_id}", response_model=DeterministicRunResponse)
    def evaluate_programme_claims(programme_id: str, hours_clause: int = 72) -> DeterministicRunResponse:
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
        return branch_svc.create_branch(payload, lineage)

    @router.post("/api/branches/{branch_id}/diff", response_model=BranchDiffResponse)
    def diff_branch(
        branch_id: str,
        view: ViewOfRisk = Query(default=ViewOfRisk.ADJUSTED),
        total_capital: float = Query(default=160.0, alias="totalCapital"),
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
    def run_frontier(req: OptimizerRequest, num_points: int = 6) -> list[FrontierPoint]:
        return compute_efficient_frontier(
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
        return compute_seasonal_earning(sim_store.get_yelt(base_sim_id), region_peril, as_of_day)

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
        return compare_model_versions(
            contract_to_programme_deal(c),
            sim_store.get_yelt(payload.oldSimSetId),
            sim_store.get_yelt(payload.newSimSetId),
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

    return router
