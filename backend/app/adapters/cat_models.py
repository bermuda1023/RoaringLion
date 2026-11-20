"""Phase 5: Model-Agnostic Cat Model API Orchestrator (RMS IRP, Verisk AIR, KCC RiskInsight, Oasis LMF)."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from app.engine.sim_generator import generate_synthetic_sim_set
from app.models.common import CamelModel
from app.models.domain import BreadcrumbStep
from app.models.enums import SimSetFormat
from app.services.lineage import LineageRegistry
from app.services.sim_sets import SimSetStore


class ModelRunRequest(CamelModel):
    vendor: str  # RMS | AIR | KCC | OASIS
    programme_id: str
    edm_database_name: str = "EDM_FARMERS_2027"
    model_version: str = "v23"
    num_trials: int = 50_000
    currency: str = "USD"


class ModelRunJob(CamelModel):
    job_id: str
    vendor: str
    model_version: str
    programme_id: str
    edm_database_name: str
    status: str  # queued | running | completed | failed
    mode: str  # mock | live
    sim_set_id: str | None = None
    manifest_id: str | None = None
    created_at: datetime


class CatModelAdapter(ABC):
    vendor_name: str

    @abstractmethod
    def run_analysis(self, req: ModelRunRequest, sim_store: SimSetStore, lineage: LineageRegistry) -> ModelRunJob:
        ...


class MockVendorAdapter(CatModelAdapter):
    """Deterministic mock adapter adhering to the RMS/AIR/KCC/Oasis async run-and-export lifecycle."""

    def __init__(self, vendor_name: str, seed_offset: int) -> None:
        self.vendor_name = vendor_name
        self.seed_offset = seed_offset

    def run_analysis(self, req: ModelRunRequest, sim_store: SimSetStore, lineage: LineageRegistry) -> ModelRunJob:
        job_id = f"job-{self.vendor_name.lower()}-{uuid.uuid4().hex[:8]}"
        sim_id = f"sim-{self.vendor_name.lower()}-{uuid.uuid4().hex[:8]}"
        table = generate_synthetic_sim_set(
            sim_set_id=sim_id,
            name=f"{self.vendor_name} {req.model_version} — {req.edm_database_name}",
            num_trials=req.num_trials,
            seed=20270000 + self.seed_offset + (abs(hash(req.edm_database_name)) % 10000),
        )
        from app.db.database import get_session

        with get_session() as db:
            sim_store._persist(db, table, SimSetFormat.YELT, is_industry=False)
            db.commit()

        manifest = lineage.record_manifest(
            run_type="CAT_MODEL_API_RUN",
            input_hashes={"edm": req.edm_database_name, "vendor": f"{self.vendor_name}:{req.model_version}"},
            parameters=req.model_dump(),
            output_payload={"sim_set_id": sim_id, "total_events": int(len(table.losses))},
            breadcrumb=[
                BreadcrumbStep(entity_type="EDM_REF", entity_id=req.edm_database_name, label=f"EDM {req.edm_database_name}"),
                BreadcrumbStep(entity_type="SIM_SET", entity_id=sim_id, label=table.name, content_hash=table.content_hash),
            ],
        )
        return ModelRunJob(
            job_id=job_id,
            vendor=self.vendor_name,
            model_version=req.model_version,
            programme_id=req.programme_id,
            edm_database_name=req.edm_database_name,
            status="completed",
            mode="mock",
            sim_set_id=sim_id,
            manifest_id=manifest.manifest_id,
            created_at=datetime.now(timezone.utc),
        )


class CatModelOrchestrator:
    def __init__(self) -> None:
        self.adapters: dict[str, CatModelAdapter] = {
            "RMS": MockVendorAdapter("RMS", 101),
            "AIR": MockVendorAdapter("AIR", 202),
            "KCC": MockVendorAdapter("KCC", 303),
            "OASIS": MockVendorAdapter("OASIS", 404),
        }
        self.jobs: list[ModelRunJob] = []

    def submit_run(self, req: ModelRunRequest, sim_store: SimSetStore, lineage: LineageRegistry) -> ModelRunJob:
        vendor = req.vendor.upper()
        if vendor not in self.adapters:
            raise ValueError(f"Unsupported vendor: {vendor}. Supported: {list(self.adapters.keys())}")
        job = self.adapters[vendor].run_analysis(req, sim_store, lineage)
        self.jobs.insert(0, job)
        return job

    def list_jobs(self) -> list[ModelRunJob]:
        return self.jobs
