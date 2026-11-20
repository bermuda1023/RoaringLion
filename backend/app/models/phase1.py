"""Phase 1 domain models: CMS contracts, Parquet SimSet/ELT ingestion, ILW pricer, sliced rollup, staleness."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .common import CamelModel
from .domain import GraphSpec, RunManifest, SimSetSummary, UpliftSet
from .enums import (
    ContractDirection,
    IlwTriggerType,
    InstrumentType,
    PerilCode,
    PlacementType,
    ProgrammeStatus,
    SimSetFormat,
    StructureType,
    ViewOfRisk,
)


class IlwSpec(CamelModel):
    industry_sim_set_id: str = "ind-synth-cat-v1-100k"
    region_perils: list[str]
    trigger_type: IlwTriggerType = IlwTriggerType.BINARY
    trigger_bn: float
    exhaustion_bn: float | None = None
    limit_m: float
    rol: float
    direction: ContractDirection = ContractDirection.CEDED if hasattr(ContractDirection, "CEDED") else ContractDirection.EXTERNAL_RETRO


class ContractCreate(CamelModel):
    programme_id: str | None = None
    chain_id: str = ""
    prior_contract_id: str | None = None
    cedent_name: str
    programme_name: str
    treaty_year: int = 2027
    renewal_period: str = "J27"
    office: str = "BDA"
    underwriter: str = "J. Anfosso"
    secondary_underwriter: str | None = None
    broker: str = "Direct"
    fronting_carrier: str | None = None
    collateral_cell: str | None = None
    status: ProgrammeStatus = ProgrammeStatus.QUOTED
    direction: ContractDirection = ContractDirection.ASSUMED
    structure: StructureType = StructureType.OCC_XL
    instrument: InstrumentType = InstrumentType.UNL
    placement: PlacementType = PlacementType.OPEN_MARKET
    inception_date: str | None = "2027-01-01"
    expiry_date: str | None = "2027-12-31"
    hours_clause: int = 72
    authorized_line: float = 1.0
    written_line: float = 1.0
    signed_line: float = 1.0
    limit: float = 25.0
    attachment: float = 50.0
    deposit_premium: float = 4.0
    rol: float = 0.16
    technical_rol: float | None = None
    brokerage_pct: float = 0.10
    fronting_fee_pct: float = 0.02
    collateral_required: float = 25.0
    currency: str = "USD"
    sim_set_id: str = "sim-synth-cat-v1-100k"
    exposure_eclipse_programme_id: str | None = None
    region_perils: list[str] = ["US-FL-WS"]
    perils: list[PerilCode] = [PerilCode.WS]
    custom_tags: list[str] = []
    uplift_set: UpliftSet | None = None
    graph: GraphSpec | None = None
    ilw_spec: dict[str, Any] | None = None


class ContractUpdate(CamelModel):
    status: ProgrammeStatus | None = None
    direction: ContractDirection | None = None
    written_line: float | None = None
    signed_line: float | None = None
    authorized_line: float | None = None
    deposit_premium: float | None = None
    limit: float | None = None
    attachment: float | None = None
    rol: float | None = None
    technical_rol: float | None = None
    brokerage_pct: float | None = None
    fronting_fee_pct: float | None = None
    collateral_required: float | None = None
    underwriter: str | None = None
    broker: str | None = None
    renewal_period: str | None = None
    custom_tags: list[str] | None = None
    exposure_eclipse_programme_id: str | None = None


class ContractFull(CamelModel):
    programme_id: str
    chain_id: str = ""
    prior_contract_id: str | None = None
    cedent_name: str
    programme_name: str
    treaty_year: int
    renewal_period: str
    office: str
    underwriter: str
    secondary_underwriter: str | None = None
    broker: str
    fronting_carrier: str | None = None
    collateral_cell: str | None = None
    status: ProgrammeStatus
    direction: ContractDirection = ContractDirection.ASSUMED
    structure: StructureType = StructureType.OCC_XL
    instrument: InstrumentType = InstrumentType.UNL
    placement: PlacementType = PlacementType.OPEN_MARKET
    inception_date: str | None = None
    expiry_date: str | None = None
    hours_clause: int = 72
    authorized_line: float = 1.0
    written_line: float = 1.0
    signed_line: float = 1.0
    limit: float = 0.0
    attachment: float = 0.0
    deposit_premium: float = 0.0
    rol: float = 0.0
    technical_rol: float | None = None
    brokerage_pct: float = 0.10
    fronting_fee_pct: float = 0.02
    collateral_required: float = 0.0
    currency: str = "USD"
    sim_set_id: str
    exposure_eclipse_programme_id: str | None = None
    region_perils: list[str] = []
    perils: list[PerilCode] = []
    custom_tags: list[str] = []
    uplift_set: UpliftSet
    graph: GraphSpec
    ilw_spec: dict[str, Any] | None = None
    version: int = 1
    content_hash: str = ""
    updated_at: datetime | None = None


class EltRowInput(CamelModel):
    event_id: int
    annual_rate: float
    mean_loss: float
    sd_indep: float = 0.0
    sd_corr: float = 0.0
    max_loss: float | None = None
    region_peril_key: str = "US-FL-WS"
    day_of_year: int = 240


class EltIngestRequest(CamelModel):
    name: str
    event_catalog_id: str = "SYNTH_CAT_V1"
    num_trials: int = 50_000
    currency: str = "USD"
    seed: int = 42
    is_industry_catalog: bool = False
    rows: list[EltRowInput]


class SimSetDetail(SimSetSummary):
    source_format: SimSetFormat = SimSetFormat.YELT
    parquet_path: str = ""
    is_industry_catalog: bool = False
    expected_annual_loss: float | None = None
    manifest: RunManifest | None = None


class IlwPriceRequest(CamelModel):
    name: str = "US FL Wind $50B Binary ILW"
    industry_sim_set_id: str = "ind-synth-cat-v1-100k"
    region_perils: list[str] = ["US-FL-WS"]
    trigger_type: IlwTriggerType = IlwTriggerType.BINARY
    trigger_bn: float = 50.0
    exhaustion_bn: float | None = None
    limit_m: float = 25.0
    rol: float = 0.22
    direction: ContractDirection = ContractDirection.EXTERNAL_RETRO
    currency: str = "USD"
    portfolio_view: ViewOfRisk = ViewOfRisk.ADJUSTED


class IlwPriceResponse(CamelModel):
    name: str
    industry_sim_set_id: str
    event_catalog_id: str
    region_perils: list[str]
    trigger_type: IlwTriggerType
    trigger_bn: float
    exhaustion_bn: float | None = None
    limit_m: float
    premium_m: float
    rol: float
    direction: ContractDirection
    currency: str
    compute_ms: float
    # Standalone metrics
    attach_prob_pct: float
    exhaust_prob_pct: float
    expected_loss_m: float
    sd_m: float
    elr_pct: float
    # Live portfolio hedge & basis-risk metrics
    indemnity_index_correlation: float
    gross_portfolio_tvar_250_m: float
    net_portfolio_tvar_250_m: float
    tvar_250_reduction_m: float
    hedge_effectiveness_pct: float
    basis_risk_residual_m: float
    cost_per_dollar_tvar_saved: float | None = None
    manifest: RunManifest


class SliceRollupRequest(CamelModel):
    portfolio_name: str = "Custom CMS Slice Rollup"
    view: ViewOfRisk = ViewOfRisk.ADJUSTED
    total_capital: float = 160.0
    statuses: list[ProgrammeStatus] | None = None
    directions: list[ContractDirection] | None = None
    underwriters: list[str] | None = None
    offices: list[str] | None = None
    renewal_periods: list[str] | None = None
    region_perils: list[str] | None = None
    programme_ids: list[str] | None = None


class StaleManifestInfo(CamelModel):
    manifest_id: str
    run_type: str
    is_stale: bool
    stale_reasons: list[str] = []
