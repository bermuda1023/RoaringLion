"""Phase 1 domain models: CMS contracts, Parquet SimSet/ELT ingestion, ILW pricer, sliced rollup, staleness."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

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


class ProgrammeProfile(CamelModel):
    """Institutional underwriting programme metadata (unique code, counterparty, team, RARC, coverage, files)."""

    # Identification & classification
    unique_code: str = ""
    program_code: str = ""
    legal_entity: str = "RoaringLion Re Transformer"
    trading_unit: str = "N.A. Property"
    planning_class: str = "Property CAT"
    new_or_renewal: str = "RENEWAL"  # NEW | RENEWAL
    is_multi_year: bool = False
    written_date: str | None = None
    external_file_path: str | None = None

    # Counterparty, broker & credit
    cedent_group: str = ""
    cedent_country: str = "USA"
    cedent_credit_rating: str = "AM Best A-"
    broker_group: str = ""
    closing_broker: str | None = None
    broker_contact: str | None = None
    mga_code: str | None = None

    # Underwriting & analytics team
    underwriter_assistant: str | None = None
    pricing_analyst: str | None = None
    claim_handler: str | None = None

    # Line & coverage mechanics
    offered_line: float | None = None
    estimated_signed_pct: float = 1.0
    limit_basis: str = "UNL"  # UNL | INDUSTRY_INDEX | PARAMETRIC
    claim_trigger: str = "LOSSES_OCCURRING"  # LOSSES_OCCURRING | RISK_ATTACHING
    loss_event_type: str = "1ST_LOSS"  # 1ST_LOSS | 2ND_LOSS | AGGREGATE
    reinstatement_basis: str = "PRO_RATA_AMOUNT"  # PRO_RATA_AMOUNT | PRO_RATA_TIME | FREE
    inuring_note: str | None = None

    # RARC (Risk-Adjusted Rate Change) & Prior UY comparison
    economic_inflation_pct: float = 3.0
    social_inflation_pct: float = 0.0
    gearing_effect_pct: float = 0.0
    rarc_exposure_pct: float = 0.0
    rarc_layer_pct: float = 0.0
    rarc_price_pct: float = 0.0
    rarc_total_pct: float = 0.0
    prior_year_limit_m: float | None = None
    prior_year_premium_m: float | None = None
    prior_year_rol_pct: float | None = None
    prior_year_signed_line: float | None = None


class ContractNoteCreate(CamelModel):
    author: str = "Lead Underwriter"
    category: str = "UNDERWRITING"  # UNDERWRITING | PRICING | BROKER | CLAIMS | MODELING
    body: str


class ContractNoteOut(CamelModel):
    note_id: str
    programme_id: str
    cedent_name: str
    author: str
    category: str
    body: str
    created_at: datetime


class LinkPriorRequest(CamelModel):
    prior_contract_id: str | None = None


class RenewProgrammeRequest(CamelModel):
    treaty_year: int | None = None
    renewal_period: str | None = None
    limit: float | None = None
    attachment: float | None = None
    rol: float | None = None
    signed_line: float | None = None
    rarc_total_pct: float | None = None


class ContractLayer(CamelModel):
    node_id: str
    layer_code: str = ""
    label: str
    node_type: str
    limit: float | None = None
    attachment: float | None = None
    deposit_premium: float = 0.0
    rol: float | None = None
    reinstatements: float | None = None
    rip_rate: float | None = None
    authorized_line: float = 1.0
    written_line: float
    signed_line: float
    our_limit: float | None = None
    our_premium: float = 0.0
    brokerage_pct: float = 0.10
    our_net_premium: float = 0.0
    technical_rol: float | None = None
    price_adequacy: float | None = None
    editable: list[str] = []
    name: str = ""
    terms: str | None = None
    auto_label: bool = True
    linked_pricing_node_id: str | None = None
    linked_pricing_label: str | None = None
    linked_feeder_node_id: str | None = None
    linked_feeder_label: str | None = None


class ContractLayerUpdate(CamelModel):
    node_id: str
    layer_code: str | None = None
    label: str | None = None
    limit: float | None = None
    attachment: float | None = None
    deposit_premium: float | None = None
    reinstatements: float | None = None
    rip_rate: float | None = None
    rol: float | None = None
    authorized_line: float | None = None
    written_line: float | None = None
    signed_line: float | None = None
    brokerage_pct: float | None = None
    technical_rol: float | None = None


class ContractLayerSpec(CamelModel):
    """A new excess-of-loss layer on a programme (amounts in $M at 100%, lines as 0..1)."""

    layer_code: str | None = None
    name: str | None = None
    layer_type: Literal["OCC_XL", "AGG_XL"] = "OCC_XL"
    limit: float = Field(gt=0, allow_inf_nan=False)
    attachment: float = Field(ge=0, allow_inf_nan=False)
    deposit_premium: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    rol: float | None = Field(default=None, ge=0, le=5, description="Rate on line; sets the premium when given")
    reinstatements: float | None = Field(default=1.0, ge=0)
    rip_rate: float = Field(default=1.0, ge=0, le=5)
    per_event_cap: float | None = Field(default=None, gt=0)
    authorized_line: float | None = Field(default=None, ge=0, le=1)
    written_line: float | None = Field(default=None, ge=0, le=1)
    signed_line: float | None = Field(default=None, ge=0, le=1)
    brokerage_pct: float | None = Field(default=None, ge=0, le=0.9)
    technical_rol: float | None = Field(default=None, ge=0, le=5)


class ContractLayersUpdateRequest(CamelModel):
    layers: list[ContractLayerUpdate]


class RenewalChainRow(CamelModel):
    programme_id: str
    unique_code: str
    programme_name: str
    treaty_year: int
    renewal_period: str
    status: str
    prior_contract_id: str | None = None
    limit: float
    attachment: float
    deposit_premium: float
    rol_pct: float
    signed_line: float
    rarc_total_pct: float
    yoy_limit_delta_m: float | None = None
    yoy_rol_delta_pts: float | None = None
    yoy_premium_delta_m: float | None = None
    is_current: bool = False


class ContractCreate(CamelModel):
    programme_id: str | None = None
    chain_id: str = ""
    prior_contract_id: str | None = None
    cedent_name: str
    programme_name: str
    treaty_year: int = 2027
    renewal_period: str = "J27"
    office: str = "BDA"
    underwriter: str = "Lead Underwriter"
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
    profile: ProgrammeProfile | None = None
    layers: list[ContractLayerSpec] = []


class ContractUpdate(CamelModel):
    status: ProgrammeStatus | None = None
    direction: ContractDirection | None = None
    sim_set_id: str | None = None
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
    prior_contract_id: str | None = None
    chain_id: str | None = None
    profile: ProgrammeProfile | None = None


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
    profile: ProgrammeProfile = ProgrammeProfile()
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


class YeltFromNodeRequest(CamelModel):
    name: str | None = None
    view: ViewOfRisk | None = None
