"""Canonical Parquet Simulation-Set Registry, Column Mapper, and Industry Catalog Store."""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from app.db.database import get_data_dir, get_session
from app.db.models import SimSetRecord
from app.engine.elt_simulator import simulate_elt_to_yelt
from app.engine.sim_generator import REGION_PERILS, RP_TO_ID, YeltTable, generate_synthetic_sim_set
from app.models.domain import BreadcrumbStep
from app.models.enums import SimSetFormat
from app.models.phase1 import EltIngestRequest, LossFileRecord, SimSetDetail
from app.services.loss_files import files_from_keys, files_from_stored, loss_file
from app.services.lineage import LineageRegistry, canonical_hash


def _sim_dir() -> Path:
    d = get_data_dir() / "sim_sets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_yelt_parquet(table: YeltTable, path: Path) -> None:
    pa_table = pa.table({
        "trial_id": pa.array(table.trial_ids, type=pa.uint32()),
        "event_id": pa.array(table.event_ids, type=pa.uint64()),
        "day_of_year": pa.array(table.day_of_year, type=pa.uint16()),
        "rp_id": pa.array(table.rp_ids, type=pa.int32()),
        "loss_amount": pa.array(table.losses, type=pa.float64()),
    })
    pq.write_table(pa_table, path, compression="zstd")


def read_yelt_parquet(rec: SimSetRecord) -> YeltTable:
    pa_table = pq.read_table(rec.parquet_path)
    return YeltTable(
        sim_set_id=rec.sim_set_id,
        name=rec.name,
        event_catalog_id=rec.event_catalog_id,
        num_trials=rec.num_trials,
        currency=rec.currency,
        trial_ids=np.ascontiguousarray(pa_table["trial_id"].to_numpy(), dtype=np.uint32),
        event_ids=np.ascontiguousarray(pa_table["event_id"].to_numpy(), dtype=np.uint64),
        day_of_year=np.ascontiguousarray(pa_table["day_of_year"].to_numpy(), dtype=np.uint16),
        rp_ids=np.ascontiguousarray(pa_table["rp_id"].to_numpy(), dtype=np.int32),
        losses=np.ascontiguousarray(pa_table["loss_amount"].to_numpy(), dtype=np.float64),
        content_hash=rec.content_hash,
    )


def build_aligned_industry_catalog(base_yelt: YeltTable, seed: int = 20269999) -> YeltTable:
    """Build an event-aligned Industry Loss YELT ($B) sharing the exact (trial_id, event_id) space.

    Industry losses are correlated with cedent indemnity losses (realistic market-share scaling
    plus idiosyncratic basis noise) so ILW basis risk & hedge effectiveness are authentic.
    """
    rng = np.random.default_rng(seed)
    # Market-share inverse multipliers converting cedent $M loss to industry $B loss
    # e.g., $100M cedent FL wind loss ~ $50B industry event on average
    rp_scale_to_bn = np.array([0.48, 0.42, 0.38, 0.55, 0.50, 0.32], dtype=np.float64)
    scales = rp_scale_to_bn[np.clip(base_yelt.rp_ids, 0, len(rp_scale_to_bn) - 1)]
    # LogNormal basis noise (mean ~ 1.0, CV ~ 22%) so indemnity vs index has realistic basis risk
    basis_noise = rng.lognormal(mean=-0.024, sigma=0.22, size=len(base_yelt.losses))
    ind_losses_bn = np.ascontiguousarray(base_yelt.losses * scales * basis_noise, dtype=np.float64)

    sim_id = "ind-synth-cat-v1-100k"
    chash = canonical_hash({
        "sim_set_id": sim_id,
        "parent_catalog": base_yelt.event_catalog_id,
        "num_trials": base_yelt.num_trials,
        "sum_bn": round(float(ind_losses_bn.sum()), 4),
    })
    return YeltTable(
        sim_set_id=sim_id,
        name="Background Industry Loss Catalog — SYNTH_CAT_V1 ($B)",
        event_catalog_id=base_yelt.event_catalog_id,
        num_trials=base_yelt.num_trials,
        currency="USD",
        trial_ids=base_yelt.trial_ids.copy(),
        event_ids=base_yelt.event_ids.copy(),
        day_of_year=base_yelt.day_of_year.copy(),
        rp_ids=base_yelt.rp_ids.copy(),
        losses=ind_losses_bn,
        content_hash=chash,
    )


def _loss_file_models(rec: SimSetRecord) -> list[LossFileRecord]:
    files = files_from_stored(
        getattr(rec, "loss_files_json", "[]"),
        json.loads(rec.region_perils_json or "[]"),
        sim_set_id=rec.sim_set_id,
        name=rec.name,
    )
    return [LossFileRecord.model_validate(item) for item in files]


class SimSetStore:
    """Persistent Parquet + SQLite registry for YELT, ELT-simulated, and Industry simulation sets."""

    def __init__(self) -> None:
        self._cache: dict[str, YeltTable] = {}

    def ensure_seeded(self, num_trials: int = 100_000) -> tuple[YeltTable, YeltTable]:
        with get_session() as db:
            base_rec = db.get(SimSetRecord, "sim-synth-cat-v1-100k")
            ind_rec = db.get(SimSetRecord, "ind-synth-cat-v1-100k")

            if base_rec and Path(base_rec.parquet_path).exists():
                base_yelt = self.get_yelt(base_rec.sim_set_id)
            else:
                base_yelt = generate_synthetic_sim_set(num_trials=num_trials)
                self._persist(db, base_yelt, SimSetFormat.SYNTHETIC, is_industry=False)

            if ind_rec and Path(ind_rec.parquet_path).exists():
                ind_yelt = self.get_yelt(ind_rec.sim_set_id)
            else:
                ind_yelt = build_aligned_industry_catalog(base_yelt)
                self._persist(db, ind_yelt, SimSetFormat.SYNTHETIC, is_industry=True)

            db.commit()
            return base_yelt, ind_yelt

    def _persist(
        self,
        db,
        table: YeltTable,
        fmt: SimSetFormat,
        *,
        is_industry: bool,
        loss_files: list[dict] | None = None,
        vendor: str | None = None,
    ) -> SimSetRecord:
        p = _sim_dir() / f"{table.sim_set_id}.parquet"
        write_yelt_parquet(table, p)
        unique_rps = sorted({REGION_PERILS[int(i)] for i in np.unique(table.rp_ids) if 0 <= int(i) < len(REGION_PERILS)})
        files = loss_files if loss_files is not None else files_from_keys(
            unique_rps, sim_set_id=table.sim_set_id, name=table.name, vendor=vendor,
        )
        file_keys = sorted({str(item.get("regionPeril")) for item in files if item.get("regionPeril")})
        rec = SimSetRecord(
            sim_set_id=table.sim_set_id,
            name=table.name,
            source_format=fmt.value,
            event_catalog_id=table.event_catalog_id,
            num_trials=table.num_trials,
            total_events=int(len(table.losses)),
            currency=table.currency,
            region_perils_json=json.dumps(file_keys or unique_rps or REGION_PERILS),
            loss_files_json=json.dumps(files),
            parquet_path=str(p),
            content_hash=table.content_hash,
            is_industry_catalog=is_industry,
        )
        db.merge(rec)
        self._cache[table.sim_set_id] = table
        return rec

    def get_yelt(self, sim_set_id: str) -> YeltTable:
        if sim_set_id in self._cache:
            return self._cache[sim_set_id]
        with get_session() as db:
            rec = db.get(SimSetRecord, sim_set_id)
            if not rec:
                raise KeyError(f"SimSet not found: {sim_set_id}")
            table = read_yelt_parquet(rec)
            self._cache[sim_set_id] = table
            return table

    def list_sim_sets(self, *, industry_only: bool | None = None) -> list[SimSetDetail]:
        with get_session() as db:
            q = db.query(SimSetRecord)
            if industry_only is True:
                q = q.filter(SimSetRecord.is_industry_catalog.is_(True))
            elif industry_only is False:
                q = q.filter(SimSetRecord.is_industry_catalog.is_(False))
            recs = q.order_by(SimSetRecord.created_at.desc()).all()
            out: list[SimSetDetail] = []
            for r in recs:
                aal = None
                if r.sim_set_id in self._cache:
                    t = self._cache[r.sim_set_id]
                    aal = round(float(t.losses.sum()) / max(t.num_trials, 1), 4)
                out.append(
                    SimSetDetail(
                        sim_set_id=r.sim_set_id,
                        name=r.name,
                        source_format=SimSetFormat(r.source_format),
                        event_catalog_id=r.event_catalog_id,
                        num_trials=r.num_trials,
                        total_events=r.total_events,
                        currency=r.currency,
                        region_perils=json.loads(r.region_perils_json),
                        loss_files=_loss_file_models(r),
                        content_hash=r.content_hash,
                        parquet_path=r.parquet_path,
                        is_industry_catalog=r.is_industry_catalog,
                        expected_annual_loss=aal,
                    )
                )
            return out

    def ingest_yelt_bytes(
        self,
        *,
        raw_bytes: bytes,
        filename: str,
        name: str,
        event_catalog_id: str,
        num_trials: int,
        currency: str,
        trial_col: str,
        event_col: str,
        loss_col: str,
        day_col: str | None,
        region_peril_col: str | None,
        default_region_peril: str,
        is_industry_catalog: bool,
        lineage: LineageRegistry,
        region: str | None = None,
        peril: str | None = None,
        vendor: str | None = None,
    ) -> SimSetDetail:
        if filename.lower().endswith(".parquet"):
            df = pl.read_parquet(io.BytesIO(raw_bytes))
        else:
            df = pl.read_csv(io.BytesIO(raw_bytes))

        trial_ids = df[trial_col].cast(pl.UInt32).to_numpy()
        event_ids = df[event_col].cast(pl.UInt64).to_numpy()
        losses = df[loss_col].cast(pl.Float64).to_numpy()
        if day_col and day_col in df.columns:
            days = df[day_col].cast(pl.UInt16).to_numpy()
        else:
            days = np.full(len(losses), 240, dtype=np.uint16)

        declared_region = (region or "").strip()
        declared_peril = (peril or "").strip()
        declared = bool(declared_region and declared_peril)
        if declared and not (region_peril_col and region_peril_col in df.columns):
            default_region_peril = f"{declared_region}-{declared_peril}"
        if region_peril_col and region_peril_col in df.columns and not declared:
            rp_strs = df[region_peril_col].cast(pl.Utf8).to_list()
            rp_ids = np.array([RP_TO_ID.get(s, 0) for s in rp_strs], dtype=np.int32)
            file_keys = [str(s) for s in rp_strs if s]
        else:
            rp_ids = np.full(len(losses), RP_TO_ID.get(default_region_peril, 0), dtype=np.int32)
            file_keys = [default_region_peril]

        order = np.lexsort((event_ids, days, trial_ids))
        trial_ids = np.ascontiguousarray(trial_ids[order], dtype=np.uint32)
        event_ids = np.ascontiguousarray(event_ids[order], dtype=np.uint64)
        days = np.ascontiguousarray(days[order], dtype=np.uint16)
        rp_ids = np.ascontiguousarray(rp_ids[order], dtype=np.int32)
        losses = np.ascontiguousarray(losses[order], dtype=np.float64)

        sim_id = f"{'ind' if is_industry_catalog else 'sim'}-{uuid.uuid4().hex[:10]}"
        chash = canonical_hash({
            "sim_set_id": sim_id,
            "filename": filename,
            "total_events": int(len(losses)),
            "sum_loss": round(float(losses.sum()), 4),
        })
        table = YeltTable(
            sim_set_id=sim_id,
            name=name,
            event_catalog_id=event_catalog_id,
            num_trials=num_trials,
            currency=currency,
            trial_ids=trial_ids,
            event_ids=event_ids,
            day_of_year=days,
            rp_ids=rp_ids,
            losses=losses,
            content_hash=chash,
        )

        if declared:
            uploaded = [loss_file(sim_set_id=sim_id, name=name, region=declared_region, peril=declared_peril, vendor=vendor)]
        else:
            uploaded = files_from_keys(file_keys, sim_set_id=sim_id, name=name, vendor=vendor)
        with get_session() as db:
            rec = self._persist(db, table, SimSetFormat.YELT, is_industry=is_industry_catalog, loss_files=uploaded, vendor=vendor)
            db.commit()

        aal = round(float(losses.sum()) / max(num_trials, 1), 4)
        manifest = lineage.record_manifest(
            run_type="SIM_SET_INGEST_YELT",
            input_hashes={"upload": chash},
            parameters={"filename": filename, "num_trials": num_trials, "event_catalog_id": event_catalog_id},
            output_payload={"sim_set_id": sim_id, "aal": aal, "total_events": int(len(losses))},
            breadcrumb=[BreadcrumbStep(entity_type="SIM_SET", entity_id=sim_id, label=name, content_hash=chash)],
        )
        return SimSetDetail(
            sim_set_id=rec.sim_set_id,
            name=rec.name,
            source_format=SimSetFormat.YELT,
            event_catalog_id=rec.event_catalog_id,
            num_trials=rec.num_trials,
            total_events=rec.total_events,
            currency=rec.currency,
            region_perils=json.loads(rec.region_perils_json),
            loss_files=_loss_file_models(rec),
            content_hash=rec.content_hash,
            parquet_path=rec.parquet_path,
            is_industry_catalog=rec.is_industry_catalog,
            expected_annual_loss=aal,
            manifest=manifest,
        )

    def ingest_elt(self, req: EltIngestRequest, lineage: LineageRegistry) -> SimSetDetail:
        sim_id = f"{'ind' if req.is_industry_catalog else 'sim'}-elt-{uuid.uuid4().hex[:8]}"
        table, theoretical_aal = simulate_elt_to_yelt(
            sim_set_id=sim_id,
            name=req.name,
            event_catalog_id=req.event_catalog_id,
            rows=req.rows,
            num_trials=req.num_trials,
            currency=req.currency,
            seed=req.seed,
        )
        declared_region = (req.region or "").strip()
        declared_peril = (req.peril or "").strip()
        if declared_region and declared_peril:
            uploaded = [loss_file(
                sim_set_id=sim_id, name=req.name, region=declared_region, peril=declared_peril, vendor=req.vendor,
            )]
        else:
            uploaded = files_from_keys(
                [row.region_peril_key for row in req.rows],
                sim_set_id=sim_id,
                name=req.name,
                vendor=req.vendor,
            )
        with get_session() as db:
            rec = self._persist(db, table, SimSetFormat.ELT, is_industry=req.is_industry_catalog, loss_files=uploaded, vendor=req.vendor)
            db.commit()

        sim_aal = round(float(table.losses.sum()) / max(req.num_trials, 1), 4)
        manifest = lineage.record_manifest(
            run_type="SIM_SET_INGEST_ELT",
            input_hashes={"elt_rows": canonical_hash([r.model_dump() for r in req.rows])},
            parameters={"num_trials": req.num_trials, "seed": req.seed, "event_catalog_id": req.event_catalog_id},
            output_payload={"sim_set_id": sim_id, "simulated_aal": sim_aal, "theoretical_aal": round(theoretical_aal, 4)},
            breadcrumb=[BreadcrumbStep(entity_type="SIM_SET", entity_id=sim_id, label=req.name, content_hash=table.content_hash)],
        )
        return SimSetDetail(
            sim_set_id=rec.sim_set_id,
            name=rec.name,
            source_format=SimSetFormat.ELT,
            event_catalog_id=rec.event_catalog_id,
            num_trials=rec.num_trials,
            total_events=rec.total_events,
            currency=rec.currency,
            region_perils=json.loads(rec.region_perils_json),
            loss_files=_loss_file_models(rec),
            content_hash=rec.content_hash,
            parquet_path=rec.parquet_path,
            is_industry_catalog=rec.is_industry_catalog,
            expected_annual_loss=sim_aal,
            manifest=manifest,
        )

    def register_derived(
        self,
        table: YeltTable,
        lineage: LineageRegistry,
        *,
        breadcrumb: list[BreadcrumbStep],
        parameters: dict,
    ) -> SimSetDetail:
        """Persist a YELT produced by a graph node so it can be priced, exported or reused like any sim set."""
        with get_session() as db:
            rec = self._persist(db, table, SimSetFormat.DERIVED, is_industry=False)
            db.commit()
        aal = round(float(table.losses.sum()) / max(table.num_trials, 1), 4)
        manifest = lineage.record_manifest(
            run_type="SIM_SET_FROM_NODE",
            input_hashes={step.entity_type.lower(): step.content_hash for step in breadcrumb if step.content_hash},
            parameters=parameters,
            output_payload={"sim_set_id": table.sim_set_id, "aal": aal, "total_events": int(len(table.losses))},
            breadcrumb=[
                *breadcrumb,
                BreadcrumbStep(entity_type="SIM_SET", entity_id=table.sim_set_id, label=table.name, content_hash=table.content_hash),
            ],
        )
        return SimSetDetail(
            sim_set_id=rec.sim_set_id,
            name=rec.name,
            source_format=SimSetFormat.DERIVED,
            event_catalog_id=rec.event_catalog_id,
            num_trials=rec.num_trials,
            total_events=rec.total_events,
            currency=rec.currency,
            region_perils=json.loads(rec.region_perils_json),
            loss_files=_loss_file_models(rec),
            content_hash=rec.content_hash,
            parquet_path=rec.parquet_path,
            is_industry_catalog=False,
            expected_annual_loss=aal,
            manifest=manifest,
        )
