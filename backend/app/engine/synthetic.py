"""Phase 5: Synthetic Frequency-Severity-Seasonality Loss Generator (TigerEye-inspired)."""

from __future__ import annotations

import uuid
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from app.engine.sim_generator import RP_TO_ID, YeltTable
from app.models.common import CamelModel
from app.models.enums import SimSetFormat
from app.models.phase1 import SimSetDetail
from app.services.lineage import LineageRegistry, canonical_hash
from app.services.sim_sets import SimSetStore


class SyntheticLossRequest(CamelModel):
    name: str = "AUS_WF Synthetic Loss Set"
    region_peril_key: str = "US-FL-WS"
    event_catalog_id: str = "SYNTH_CAT_V1"
    num_trials: int = Field(default=50_000, ge=100, le=200_000)
    currency: str = "USD"
    frequency_dist: Literal["POISSON", "NEG_BIN"] = "POISSON"
    mean_frequency: float = Field(default=2.5, ge=0, le=1000, allow_inf_nan=False)
    dispersion_k: float = Field(default=2.0, gt=0, le=1000, allow_inf_nan=False)  # for NEG_BIN
    severity_dist: Literal["LOGNORMAL", "NORMAL", "PARETO"] = "LOGNORMAL"
    param_mu: float = Field(default=15.0, ge=0, le=1_000_000, allow_inf_nan=False)
    param_sigma: float = Field(default=22.0, gt=0, le=1_000_000, allow_inf_nan=False)
    min_loss: float | None = Field(default=0.5, ge=0, allow_inf_nan=False)
    max_loss: float | None = Field(default=250.0, gt=0, allow_inf_nan=False)
    # 12 monthly weights summing to ~100%
    monthly_shares: list[float] = [8.33] * 12
    seed: int = 2027

    @model_validator(mode="after")
    def _check_loss_bounds(self):
        if self.min_loss is not None and self.max_loss is not None and self.max_loss < self.min_loss:
            raise ValueError("maxLoss must be >= minLoss")
        if len(self.monthly_shares) != 12:
            raise ValueError("monthlyShares must contain exactly 12 values")
        return self


def generate_synthetic_freq_sev(
    req: SyntheticLossRequest,
    sim_store: SimSetStore,
    lineage: LineageRegistry,
) -> SimSetDetail:
    rng = np.random.default_rng(req.seed)
    n = req.num_trials

    if req.frequency_dist == "NEG_BIN":
        p = req.dispersion_k / (req.dispersion_k + req.mean_frequency)
        counts = rng.negative_binomial(req.dispersion_k, p, size=n)
    else:
        counts = rng.poisson(req.mean_frequency, size=n)

    total = int(counts.sum())
    trial_ids = np.repeat(np.arange(1, n + 1, dtype=np.uint32), counts)

    if total > 0:
        if req.severity_dist == "NORMAL":
            sev = np.maximum(rng.normal(req.param_mu, req.param_sigma, size=total), 0.0)
        elif req.severity_dist == "PARETO":
            alpha = max(1.1, req.param_sigma)
            u = rng.uniform(0.0, 0.99999, size=total)
            sev = req.param_mu * (1.0 - u) ** (-1.0 / alpha)
        else:
            cv2 = (req.param_sigma / max(req.param_mu, 1e-6)) ** 2
            s2 = float(np.log(1.0 + cv2))
            sev = rng.lognormal(mean=float(np.log(max(req.param_mu, 1e-6)) - 0.5 * s2), sigma=float(np.sqrt(s2)), size=total)

        if req.min_loss is not None:
            sev = np.maximum(sev, req.min_loss)
        if req.max_loss is not None:
            sev = np.minimum(sev, req.max_loss)

        weights = np.array(req.monthly_shares if len(req.monthly_shares) == 12 else [1.0] * 12, dtype=np.float64)
        weights = np.maximum(weights, 0.0)
        weights = weights / weights.sum() if weights.sum() > 0 else np.full(12, 1.0 / 12.0)
        months = rng.choice(12, size=total, p=weights)
        days = np.clip(months * 30 + rng.integers(1, 30, size=total), 1, 365).astype(np.uint16)
        ev_ids = np.arange(80_000_000, 80_000_000 + total, dtype=np.uint64)
        rp_ids = np.full(total, RP_TO_ID.get(req.region_peril_key, 0), dtype=np.int32)

        order = np.lexsort((ev_ids, days, trial_ids))
        trial_ids = np.ascontiguousarray(trial_ids[order], dtype=np.uint32)
        ev_ids = np.ascontiguousarray(ev_ids[order], dtype=np.uint64)
        days = np.ascontiguousarray(days[order], dtype=np.uint16)
        rp_ids = np.ascontiguousarray(rp_ids[order], dtype=np.int32)
        sev = np.ascontiguousarray(sev[order], dtype=np.float64)
    else:
        ev_ids = np.zeros(0, dtype=np.uint64)
        days = np.zeros(0, dtype=np.uint16)
        rp_ids = np.zeros(0, dtype=np.int32)
        sev = np.zeros(0, dtype=np.float64)

    sim_id = f"sim-fs-{uuid.uuid4().hex[:8]}"
    chash = canonical_hash({"req": req.model_dump(), "total": total, "sum": round(float(sev.sum()), 4)})
    table = YeltTable(
        sim_set_id=sim_id,
        name=req.name,
        event_catalog_id=req.event_catalog_id,
        num_trials=n,
        currency=req.currency,
        trial_ids=trial_ids,
        event_ids=ev_ids,
        day_of_year=days,
        rp_ids=rp_ids,
        losses=sev,
        content_hash=chash,
    )

    from app.db.database import get_session
    from app.models.domain import BreadcrumbStep

    with get_session() as db:
        rec = sim_store._persist(db, table, SimSetFormat.SYNTHETIC, is_industry=False)
        db.commit()

    aal = round(float(sev.sum()) / max(n, 1), 4)
    manifest = lineage.record_manifest(
        run_type="SYNTHETIC_FREQ_SEV",
        input_hashes={"params": canonical_hash(req.model_dump())},
        parameters=req.model_dump(),
        output_payload={"sim_set_id": sim_id, "aal": aal},
        breadcrumb=[BreadcrumbStep(entity_type="SIM_SET", entity_id=sim_id, label=req.name, content_hash=chash)],
    )
    return SimSetDetail(
        sim_set_id=rec.sim_set_id,
        name=rec.name,
        source_format=SimSetFormat.SYNTHETIC,
        event_catalog_id=rec.event_catalog_id,
        num_trials=rec.num_trials,
        total_events=rec.total_events,
        currency=rec.currency,
        region_perils=[req.region_peril_key],
        content_hash=rec.content_hash,
        parquet_path=rec.parquet_path,
        is_industry_catalog=False,
        expected_annual_loss=aal,
        manifest=manifest,
    )
