"""Phase 3b: All-Direction Backtest Matrix & Scenario Forge (Invent, Perturb, Cluster, Reverse Stress)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import Field, model_validator

from app.db.database import get_session
from app.db.models import ScenarioRecord
from app.engine.burn_cost import HISTORICAL_BENCHMARK_EVENTS, group_by_hours_clause
from app.engine.slice_runner import evaluate_deterministic_run
from app.models.common import CamelModel
from app.models.domain import BreadcrumbStep, DeterministicLossRow, DeterministicRunRequest, ProgrammeDeal, RunManifest
from app.models.enums import LossSourceType, ViewOfRisk
from app.services.branching import BranchOut, apply_branch_overrides
from app.services.lineage import LineageRegistry, canonical_hash


class BacktestMatrixRequest(CamelModel):
    branch_id: str | None = None
    start_year: int = Field(default=1990, ge=1900, le=2100)
    end_year: int = Field(default=2025, ge=1900, le=2100)
    total_capital: float = Field(default=160.0, gt=0, le=1_000_000, allow_inf_nan=False)
    annual_trend: float = Field(default=0.03, ge=-0.5, le=1, allow_inf_nan=False)
    as_is_trended: bool = True

    @model_validator(mode="after")
    def _check_years(self):
        if self.end_year < self.start_year:
            raise ValueError("endYear must be >= startYear")
        return self


class BacktestYearCell(CamelModel):
    year: int
    events: list[str]
    baseline_ceded_m: float
    baseline_roc_pct: float
    branch_ceded_m: float | None = None
    branch_roc_pct: float | None = None


class BacktestMatrixResponse(CamelModel):
    branch_id: str | None
    as_is_trended: bool
    baseline_avg_roc_pct: float
    branch_avg_roc_pct: float | None
    worst_year_baseline: int
    worst_year_roc_pct: float
    years: list[BacktestYearCell]
    manifest: RunManifest


def run_all_direction_backtest(
    *,
    req: BacktestMatrixRequest,
    baseline_programmes: list[ProgrammeDeal],
    branch: BranchOut | None,
    lineage: LineageRegistry,
) -> BacktestMatrixResponse:
    branch_progs = apply_branch_overrides(baseline_programmes, branch) if branch else None
    gross_prem_base = sum(p.deposit_premium * p.signed_line for p in baseline_programmes)
    gross_prem_br = sum(p.deposit_premium * p.signed_line for p in branch_progs) if branch_progs else 0.0
    yield_m = req.total_capital * 0.03

    by_year: dict[int, list[dict[str, Any]]] = {y: [] for y in range(req.start_year, req.end_year + 1)}
    for ev in HISTORICAL_BENCHMARK_EVENTS:
        if req.start_year <= ev["year"] <= req.end_year:
            by_year[ev["year"]].append(ev)

    cells: list[BacktestYearCell] = []
    for yr in range(req.start_year, req.end_year + 1):
        evs = by_year[yr]
        mult = ((1.0 + req.annual_trend) ** max(0, 2027 - yr)) if req.as_is_trended else 1.0
        det_rows = [
            DeterministicLossRow(
                loss_id=e["eventId"],
                event_name=e["eventName"],
                occurrence_datetime=e["date"],
                region_peril_key=e["regionPerilKey"],
                ground_up_loss=round(e["groundUpLoss"] * mult, 4),
                source_type=LossSourceType.HISTORICAL_EVENT,
            )
            for e in evs
        ]

        base_loss = 0.0
        if det_rows:
            for p in baseline_programmes:
                res = evaluate_deterministic_run(
                    DeterministicRunRequest(programme_id=p.programme_id, view=ViewOfRisk.UNADJUSTED, losses=det_rows),
                    p,
                    LineageRegistry(persist=False),
                )
                base_loss += res.total_ceded
        base_roc = ((gross_prem_base + yield_m - base_loss) / req.total_capital) * 100.0

        br_loss = None
        br_roc = None
        if branch_progs is not None:
            br_loss = 0.0
            if det_rows:
                for p in branch_progs:
                    res = evaluate_deterministic_run(
                        DeterministicRunRequest(programme_id=p.programme_id, view=ViewOfRisk.UNADJUSTED, losses=det_rows),
                        p,
                        LineageRegistry(persist=False),
                    )
                    br_loss += res.total_ceded
            br_roc = ((gross_prem_br + yield_m - br_loss) / req.total_capital) * 100.0

        cells.append(
            BacktestYearCell(
                year=yr,
                events=[e["eventName"] for e in evs],
                baseline_ceded_m=round(base_loss, 4),
                baseline_roc_pct=round(base_roc, 2),
                branch_ceded_m=round(br_loss, 4) if br_loss is not None else None,
                branch_roc_pct=round(br_roc, 2) if br_roc is not None else None,
            )
        )

    base_avg = sum(c.baseline_roc_pct for c in cells) / max(len(cells), 1)
    br_avg = (sum(c.branch_roc_pct for c in cells if c.branch_roc_pct is not None) / max(len(cells), 1)) if branch_progs is not None else None
    worst = min(cells, key=lambda c: c.baseline_roc_pct)

    manifest = lineage.record_manifest(
        run_type="BACKTEST_MATRIX",
        input_hashes={"req": canonical_hash(req.model_dump()), "branch": branch.content_hash if branch else "BASELINE"},
        parameters=req.model_dump(),
        output_payload={"baseline_avg_roc_pct": round(base_avg, 2), "worst_year": worst.year},
        breadcrumb=[
            BreadcrumbStep(entity_type="BACKTEST_CATALOG", entity_id="hist-1990-2025", label="Historical Benchmark Catalog 1990-2025"),
            BreadcrumbStep(entity_type="PORTFOLIO", entity_id=branch.branch_id if branch else "fund-main-j27", label=branch.name if branch else "RoaringLion Bound Baseline"),
        ],
    )
    return BacktestMatrixResponse(
        branch_id=req.branch_id,
        as_is_trended=req.as_is_trended,
        baseline_avg_roc_pct=round(base_avg, 2),
        branch_avg_roc_pct=round(br_avg, 2) if br_avg is not None else None,
        worst_year_baseline=worst.year,
        worst_year_roc_pct=worst.baseline_roc_pct,
        years=cells,
        manifest=manifest,
    )


class ScenarioCreate(CamelModel):
    name: str
    provenance_tag: str = "INVENTED"  # INVENTED | PERTURBED | REVERSE_STRESS
    source_event_ref: str | None = None
    description: str = ""
    losses: list[DeterministicLossRow]


class ScenarioOut(CamelModel):
    scenario_id: str
    name: str
    provenance_tag: str
    source_event_ref: str | None
    description: str
    losses: list[DeterministicLossRow]
    content_hash: str
    created_at: datetime


class ReverseStressRequest(CamelModel):
    name: str = "Reverse Stress: Fund Breakeven Breach"
    target_roc_pct: float = Field(default=0.0, ge=-100, le=100, allow_inf_nan=False)
    total_capital: float = Field(default=160.0, gt=0, le=1_000_000, allow_inf_nan=False)
    seed_region_peril: str = "US-FL-WS"
    seed_event_name: str = "Synthetic Miami Cat 5"
    base_ground_up_m: float = Field(default=100.0, gt=0, le=1_000_000, allow_inf_nan=False)


class ReverseStressResponse(CamelModel):
    scenario: ScenarioOut
    required_multiplier: float
    required_ground_up_m: float
    resulting_portfolio_loss_m: float
    resulting_roc_pct: float
    breached_target: bool
    manifest: RunManifest


class ScenarioForgeService:
    def _to_out(self, rec: ScenarioRecord) -> ScenarioOut:
        return ScenarioOut(
            scenario_id=rec.scenario_id,
            name=rec.name,
            provenance_tag=rec.provenance_tag,
            source_event_ref=rec.source_event_ref,
            description=rec.description,
            losses=[DeterministicLossRow(**r) for r in json.loads(rec.losses_json)],
            content_hash=rec.content_hash,
            created_at=rec.created_at,
        )

    def list_scenarios(self) -> list[ScenarioOut]:
        with get_session() as db:
            recs = db.query(ScenarioRecord).order_by(ScenarioRecord.created_at.desc()).all()
            return [self._to_out(r) for r in recs]

    def get_scenario(self, scenario_id: str) -> ScenarioOut | None:
        with get_session() as db:
            rec = db.get(ScenarioRecord, scenario_id)
            return self._to_out(rec) if rec else None

    def create_scenario(self, payload: ScenarioCreate) -> ScenarioOut:
        sid = f"scn-{uuid.uuid4().hex[:8]}"
        chash = canonical_hash({"name": payload.name, "tag": payload.provenance_tag, "losses": [l.model_dump() for l in payload.losses]})
        rec = ScenarioRecord(
            scenario_id=sid,
            name=payload.name,
            provenance_tag=payload.provenance_tag,
            source_event_ref=payload.source_event_ref,
            description=payload.description,
            losses_json=json.dumps([l.model_dump() for l in payload.losses]),
            content_hash=chash,
            created_at=datetime.now(timezone.utc),
        )
        with get_session() as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return self._to_out(rec)

    def reverse_stress(
        self,
        req: ReverseStressRequest,
        programmes: list[ProgrammeDeal],
        lineage: LineageRegistry,
    ) -> ReverseStressResponse:
        """Binary-search the minimum severity multiplier on a synthetic event that pushes fund ROC <= target."""
        gross_prem = sum(p.deposit_premium * p.signed_line for p in programmes)
        yield_m = req.total_capital * 0.03

        def eval_mult(mult: float) -> tuple[float, float]:
            row = DeterministicLossRow(
                loss_id="RS-1",
                event_name=req.seed_event_name,
                occurrence_datetime="2027-09-15T12:00:00Z",
                region_peril_key=req.seed_region_peril,
                ground_up_loss=round(req.base_ground_up_m * mult, 4),
                source_type=LossSourceType.SCENARIO_INVENTED,
            )
            loss = 0.0
            for p in programmes:
                res = evaluate_deterministic_run(
                    DeterministicRunRequest(programme_id=p.programme_id, view=ViewOfRisk.UNADJUSTED, losses=[row]),
                    p,
                    LineageRegistry(persist=False),
                )
                loss += res.total_ceded
            roc = ((gross_prem + yield_m - loss) / req.total_capital) * 100.0
            return loss, roc

        lo, hi = 0.1, 6.0
        for _ in range(28):
            mid = 0.5 * (lo + hi)
            _, roc = eval_mult(mid)
            if roc <= req.target_roc_pct:
                hi = mid
            else:
                lo = mid

        final_loss, final_roc = eval_mult(hi)
        scn = self.create_scenario(
            ScenarioCreate(
                name=req.name,
                provenance_tag="REVERSE_STRESS",
                source_event_ref=f"{req.seed_region_peril} ×{hi:.3f}",
                description=f"Auto-forged reverse stress targeting ROC <= {req.target_roc_pct}%",
                losses=[
                    DeterministicLossRow(
                        loss_id="RS-1",
                        event_name=req.seed_event_name,
                        occurrence_datetime="2027-09-15T12:00:00Z",
                        region_peril_key=req.seed_region_peril,
                        ground_up_loss=round(req.base_ground_up_m * hi, 4),
                        source_type=LossSourceType.SCENARIO_INVENTED,
                    )
                ],
            )
        )
        manifest = lineage.record_manifest(
            run_type="REVERSE_STRESS",
            input_hashes={"req": canonical_hash(req.model_dump()), "scenario": scn.content_hash},
            parameters=req.model_dump(),
            output_payload={"required_ground_up_m": round(req.base_ground_up_m * hi, 4), "resulting_roc_pct": round(final_roc, 2)},
            breadcrumb=[
                BreadcrumbStep(entity_type="SCENARIO_FORGE", entity_id=scn.scenario_id, label=scn.name, content_hash=scn.content_hash)
            ],
        )
        return ReverseStressResponse(
            scenario=scn,
            required_multiplier=round(hi, 4),
            required_ground_up_m=round(req.base_ground_up_m * hi, 4),
            resulting_portfolio_loss_m=round(final_loss, 4),
            resulting_roc_pct=round(final_roc, 2),
            breached_target=final_roc <= req.target_roc_pct + 0.05,
            manifest=manifest,
        )
