"""Phase 2b: Live Claims Service (Versioned Estimate History + Chronological Recovery Statements)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.db.database import get_session
from app.db.models import ClaimRecord, ClaimVersionRecord
from app.engine.burn_cost import group_by_hours_clause
from app.engine.slice_runner import evaluate_deterministic_run
from app.models.common import CamelModel
from app.models.domain import DeterministicLossRow, DeterministicRunRequest, DeterministicRunResponse, ProgrammeDeal
from app.models.enums import LossSourceType, ViewOfRisk
from app.services.lineage import LineageRegistry


class ClaimCreate(CamelModel):
    claim_no: str
    programme_id: str
    event_name: str
    occurrence_datetime: str
    region_peril_key: str
    ground_up_loss: float
    status: str = "ESTIMATE"  # ESTIMATE | CASE_RESERVE | PAID | FINAL
    currency: str = "USD"
    note: str = ""


class ClaimRevision(CamelModel):
    ground_up_loss: float
    status: str  # ESTIMATE | CASE_RESERVE | PAID | FINAL
    note: str = ""


class ClaimVersionOut(CamelModel):
    version: int
    status: str
    ground_up_loss: float
    note: str
    advised_at: datetime


class ClaimOut(CamelModel):
    claim_id: str
    claim_no: str
    programme_id: str
    event_name: str
    occurrence_datetime: str
    region_peril_key: str
    currency: str
    current_version: int
    latest_status: str
    latest_ground_up_loss: float
    versions: list[ClaimVersionOut]


class ClaimsService:
    """Versioned, append-only Live Claims ledger."""

    def _hydrate(self, db, rec: ClaimRecord) -> ClaimOut:
        vers = (
            db.query(ClaimVersionRecord)
            .filter(ClaimVersionRecord.claim_id == rec.claim_id)
            .order_by(ClaimVersionRecord.version.asc())
            .all()
        )
        latest = vers[-1] if vers else None
        return ClaimOut(
            claim_id=rec.claim_id,
            claim_no=rec.claim_no,
            programme_id=rec.programme_id,
            event_name=rec.event_name,
            occurrence_datetime=rec.occurrence_datetime,
            region_peril_key=rec.region_peril_key,
            currency=rec.currency,
            current_version=rec.current_version,
            latest_status=latest.status if latest else "ESTIMATE",
            latest_ground_up_loss=latest.ground_up_loss if latest else 0.0,
            versions=[
                ClaimVersionOut(
                    version=v.version,
                    status=v.status,
                    ground_up_loss=v.ground_up_loss,
                    note=v.note,
                    advised_at=v.advised_at,
                )
                for v in vers
            ],
        )

    def list_claims(self, programme_id: str | None = None) -> list[ClaimOut]:
        with get_session() as db:
            q = db.query(ClaimRecord)
            if programme_id:
                q = q.filter(ClaimRecord.programme_id == programme_id)
            recs = q.order_by(ClaimRecord.occurrence_datetime.asc()).all()
            return [self._hydrate(db, r) for r in recs]

    def create_claim(self, payload: ClaimCreate) -> ClaimOut:
        cid = f"clm-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc)
        with get_session() as db:
            rec = ClaimRecord(
                claim_id=cid,
                claim_no=payload.claim_no,
                programme_id=payload.programme_id,
                event_name=payload.event_name,
                occurrence_datetime=payload.occurrence_datetime,
                region_peril_key=payload.region_peril_key,
                currency=payload.currency,
                current_version=1,
                created_at=now,
            )
            ver = ClaimVersionRecord(
                claim_id=cid,
                version=1,
                status=payload.status,
                ground_up_loss=payload.ground_up_loss,
                note=payload.note,
                advised_at=now,
            )
            db.add(rec)
            db.add(ver)
            db.commit()
            return self._hydrate(db, rec)

    def revise_claim(self, claim_id: str, rev: ClaimRevision) -> ClaimOut | None:
        now = datetime.now(timezone.utc)
        with get_session() as db:
            rec = db.get(ClaimRecord, claim_id)
            if not rec:
                return None
            rec.current_version += 1
            ver = ClaimVersionRecord(
                claim_id=claim_id,
                version=rec.current_version,
                status=rev.status,
                ground_up_loss=rev.ground_up_loss,
                note=rev.note,
                advised_at=now,
            )
            db.add(ver)
            db.commit()
            return self._hydrate(db, rec)

    def evaluate_claims_for_programme(
        self,
        programme: ProgrammeDeal,
        lineage: LineageRegistry,
        hours_clause: int = 72,
    ) -> DeterministicRunResponse:
        claims = self.list_claims(programme_id=programme.programme_id)
        rows = [
            DeterministicLossRow(
                loss_id=c.claim_id,
                event_name=f"{c.event_name} [{c.claim_no} v{c.current_version}:{c.latest_status}]",
                occurrence_datetime=c.occurrence_datetime,
                region_peril_key=c.region_peril_key,
                ground_up_loss=c.latest_ground_up_loss,
                currency=c.currency,
                source_type=LossSourceType.CLAIM,
                source_ref=f"{c.claim_id}:v{c.current_version}",
            )
            for c in claims
        ]
        grouped = group_by_hours_clause(rows, hours_clause)
        return evaluate_deterministic_run(
            DeterministicRunRequest(
                programme_id=programme.programme_id,
                view=ViewOfRisk.UNADJUSTED,
                hours_clause=hours_clause,
                losses=grouped,
            ),
            programme,
            lineage,
        )
