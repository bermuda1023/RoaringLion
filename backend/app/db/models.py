"""Persistent SQLAlchemy ORM tables for CMS Contracts, SimSets, and Breadcrumb Lineage."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ContractRecord(Base):
    __tablename__ = "contracts"

    programme_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    chain_id: Mapped[str] = mapped_column(String(80), index=True, default="")
    prior_contract_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    cedent_name: Mapped[str] = mapped_column(String(160), index=True)
    programme_name: Mapped[str] = mapped_column(String(200))
    treaty_year: Mapped[int] = mapped_column(Integer, index=True)
    renewal_period: Mapped[str] = mapped_column(String(20), index=True)
    office: Mapped[str] = mapped_column(String(20), index=True)
    underwriter: Mapped[str] = mapped_column(String(100), index=True)
    secondary_underwriter: Mapped[str | None] = mapped_column(String(100), nullable=True)
    broker: Mapped[str] = mapped_column(String(120), index=True)
    fronting_carrier: Mapped[str | None] = mapped_column(String(120), nullable=True)
    collateral_cell: Mapped[str | None] = mapped_column(String(120), nullable=True)

    status: Mapped[str] = mapped_column(String(30), index=True)
    direction: Mapped[str] = mapped_column(String(30), index=True, default="ASSUMED")
    structure: Mapped[str] = mapped_column(String(30), default="OCC_XL")
    instrument: Mapped[str] = mapped_column(String(30), default="UNL")
    placement: Mapped[str] = mapped_column(String(30), default="OPEN_MARKET")

    inception_date: Mapped[str | None] = mapped_column(String(30), nullable=True)
    expiry_date: Mapped[str | None] = mapped_column(String(30), nullable=True)
    hours_clause: Mapped[int] = mapped_column(Integer, default=72)

    authorized_line: Mapped[float] = mapped_column(Float, default=1.0)
    written_line: Mapped[float] = mapped_column(Float, default=1.0)
    signed_line: Mapped[float] = mapped_column(Float, default=1.0)
    limit: Mapped[float] = mapped_column(Float, default=0.0)
    attachment: Mapped[float] = mapped_column(Float, default=0.0)
    deposit_premium: Mapped[float] = mapped_column(Float, default=0.0)
    rol: Mapped[float] = mapped_column(Float, default=0.0)
    technical_rol: Mapped[float | None] = mapped_column(Float, nullable=True)
    brokerage_pct: Mapped[float] = mapped_column(Float, default=0.10)
    fronting_fee_pct: Mapped[float] = mapped_column(Float, default=0.02)
    collateral_required: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")

    sim_set_id: Mapped[str] = mapped_column(String(80), index=True)
    exposure_eclipse_programme_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    region_perils_json: Mapped[str] = mapped_column(Text, default="[]")
    perils_json: Mapped[str] = mapped_column(Text, default="[]")
    custom_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    uplift_set_json: Mapped[str] = mapped_column(Text, default="{}")
    graph_json: Mapped[str] = mapped_column(Text, default="{}")
    ilw_spec_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SimSetRecord(Base):
    __tablename__ = "sim_sets"

    sim_set_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source_format: Mapped[str] = mapped_column(String(20), default="YELT")
    event_catalog_id: Mapped[str] = mapped_column(String(80), index=True)
    num_trials: Mapped[int] = mapped_column(Integer)
    total_events: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    region_perils_json: Mapped[str] = mapped_column(Text, default="[]")
    parquet_path: Mapped[str] = mapped_column(String(400))
    content_hash: Mapped[str] = mapped_column(String(32))
    is_industry_catalog: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class LineageManifestRecord(Base):
    __tablename__ = "lineage_manifests"

    manifest_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_type: Mapped[str] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    actor: Mapped[str] = mapped_column(String(80), default="underwriter")
    engine_version: Mapped[str] = mapped_column(String(40), default="0.1.0-rust")
    input_hashes_json: Mapped[str] = mapped_column(Text, default="{}")
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    output_hash: Mapped[str] = mapped_column(String(32))
    breadcrumb_json: Mapped[str] = mapped_column(Text, default="[]")


class LineageLinkRecord(Base):
    __tablename__ = "lineage_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(80), index=True)
    target_id: Mapped[str] = mapped_column(String(80), index=True)
    link_type: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
