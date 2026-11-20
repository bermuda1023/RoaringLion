"""Phase 3b: Versioned Portfolio Branching (Branch, Never Mutate Bound Baselines)."""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import model_validator

from app.db.database import get_session
from app.db.models import PortfolioBranchRecord
from app.engine.sim_generator import YeltTable
from app.engine.slice_runner import evaluate_portfolio_rollup
from app.models.common import CamelModel
from app.models.domain import ProgrammeDeal
from app.models.enums import LinkType, ViewOfRisk
from app.services.lineage import LineageRegistry, canonical_hash


class BranchCreate(CamelModel):
    name: str
    parent_portfolio_id: str = "fund-keystone-j27"
    description: str = ""
    # Map programmeId -> new absolute signed-line share (0.5 = take a 50% line)
    signed_line_overrides: dict[str, float] = {}
    excluded_programme_ids: list[str] = []

    @model_validator(mode="after")
    def _check_overrides(self):
        for pid, share in self.signed_line_overrides.items():
            if not (share == share and 0.0 <= share <= 1.5):  # NaN fails share==share
                raise ValueError(f"signed line override for {pid} must be between 0 and 1.5")
        overlap = set(self.signed_line_overrides) & set(self.excluded_programme_ids)
        if overlap:
            raise ValueError(f"programmes cannot be both overridden and excluded: {sorted(overlap)}")
        return self


class BranchOut(CamelModel):
    branch_id: str
    name: str
    parent_portfolio_id: str
    description: str
    signed_line_overrides: dict[str, float]
    excluded_programme_ids: list[str]
    content_hash: str
    created_at: datetime


class BranchDiffResponse(CamelModel):
    branch_id: str
    branch_name: str
    view: ViewOfRisk
    baseline_mean_roc_pct: float
    branch_mean_roc_pct: float
    delta_mean_roc_pct: float
    baseline_tvar_250: float
    branch_tvar_250: float
    delta_tvar_250: float
    baseline_expected_profit: float
    branch_expected_profit: float
    delta_expected_profit: float
    baseline_collateral: float
    branch_collateral: float
    delta_collateral: float
    branch_manifest_id: str


def apply_branch_overrides(programmes: list[ProgrammeDeal], branch: BranchOut) -> list[ProgrammeDeal]:
    """Return deep-copied programmes with branch signed-line & exclusion overrides applied (never mutates baseline)."""
    out: list[ProgrammeDeal] = []
    for p in programmes:
        if p.programme_id in branch.excluded_programme_ids:
            continue
        p_copy = copy.deepcopy(p)
        if p.programme_id in branch.signed_line_overrides:
            new_line = float(branch.signed_line_overrides[p.programme_id])
            p_copy.signed_line = new_line
            for node in p_copy.graph.nodes:
                if "placedShare" in node.params:
                    node.params["placedShare"] = new_line
            p_copy.graph.content_hash = canonical_hash({"nodes": [n.model_dump() for n in p_copy.graph.nodes]})
        out.append(p_copy)
    return out


class BranchService:
    def _to_out(self, rec: PortfolioBranchRecord) -> BranchOut:
        ov = json.loads(rec.overrides_json)
        return BranchOut(
            branch_id=rec.branch_id,
            name=rec.name,
            parent_portfolio_id=rec.parent_portfolio_id,
            description=rec.description,
            signed_line_overrides=ov.get("signedLineOverrides", {}),
            excluded_programme_ids=ov.get("excludedProgrammeIds", []),
            content_hash=rec.content_hash,
            created_at=rec.created_at,
        )

    def list_branches(self) -> list[BranchOut]:
        with get_session() as db:
            recs = db.query(PortfolioBranchRecord).order_by(PortfolioBranchRecord.created_at.desc()).all()
            return [self._to_out(r) for r in recs]

    def get_branch(self, branch_id: str) -> BranchOut | None:
        with get_session() as db:
            rec = db.get(PortfolioBranchRecord, branch_id)
            return self._to_out(rec) if rec else None

    def create_branch(self, payload: BranchCreate, lineage: LineageRegistry) -> BranchOut:
        bid = f"br-{uuid.uuid4().hex[:8]}"
        ov = {
            "signedLineOverrides": payload.signed_line_overrides,
            "excludedProgrammeIds": payload.excluded_programme_ids,
        }
        chash = canonical_hash({"parent": payload.parent_portfolio_id, "overrides": ov})
        rec = PortfolioBranchRecord(
            branch_id=bid,
            name=payload.name,
            parent_portfolio_id=payload.parent_portfolio_id,
            description=payload.description,
            overrides_json=json.dumps(ov),
            content_hash=chash,
            created_at=datetime.now(timezone.utc),
        )
        with get_session() as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
            out = self._to_out(rec)
        lineage.add_link(bid, payload.parent_portfolio_id, LinkType.BRANCHED_FROM)
        return out

    def diff_branch(
        self,
        *,
        branch: BranchOut,
        baseline_programmes: list[ProgrammeDeal],
        yelt: YeltTable,
        view: ViewOfRisk,
        total_capital: float,
        lineage: LineageRegistry,
    ) -> BranchDiffResponse:
        base_res = evaluate_portfolio_rollup(
            portfolio_id=branch.parent_portfolio_id,
            portfolio_name="Bound Baseline",
            programmes=baseline_programmes,
            yelt=yelt,
            view=view,
            total_capital=total_capital,
            lineage=lineage,
        )
        branch_progs = apply_branch_overrides(baseline_programmes, branch)
        br_res = evaluate_portfolio_rollup(
            portfolio_id=branch.branch_id,
            portfolio_name=branch.name,
            programmes=branch_progs,
            yelt=yelt,
            view=view,
            total_capital=total_capital,
            lineage=lineage,
        )
        return BranchDiffResponse(
            branch_id=branch.branch_id,
            branch_name=branch.name,
            view=view,
            baseline_mean_roc_pct=base_res.mean_roc_pct,
            branch_mean_roc_pct=br_res.mean_roc_pct,
            delta_mean_roc_pct=round(br_res.mean_roc_pct - base_res.mean_roc_pct, 2),
            baseline_tvar_250=base_res.portfolio_tvar_250,
            branch_tvar_250=br_res.portfolio_tvar_250,
            delta_tvar_250=round(br_res.portfolio_tvar_250 - base_res.portfolio_tvar_250, 4),
            baseline_expected_profit=base_res.expected_profit,
            branch_expected_profit=br_res.expected_profit,
            delta_expected_profit=round(br_res.expected_profit - base_res.expected_profit, 4),
            baseline_collateral=base_res.deployed_collateral,
            branch_collateral=br_res.deployed_collateral,
            delta_collateral=round(br_res.deployed_collateral - base_res.deployed_collateral, 4),
            branch_manifest_id=br_res.manifest.manifest_id,
        )
