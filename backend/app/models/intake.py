"""Pydantic models for the unified Submission Intake Pipeline (Drop-in, Email, Folder doors)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from .common import CamelModel
from .domain import GraphSpec, RunManifest
from .phase1 import ContractFull, LossFileRecord


class IntakeDoor(str, Enum):
    DROP_IN = "DROP_IN"
    EMAIL = "EMAIL"
    FOLDER = "FOLDER"


class IntakeDraftStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    CONFIRMED = "CONFIRMED"
    DISCARDED = "DISCARDED"


class IntakeFileKind(str, Enum):
    YELT = "YELT"
    ELT = "ELT"
    PDF = "PDF"
    EXCEL = "EXCEL"
    TEXT = "TEXT"


class IntakeLayerDraft(CamelModel):
    layer_code: str = "L1"
    name: str = ""
    layer_type: Literal["OCC_XL", "AGG_XL", "ILW_OPTION"] = "OCC_XL"
    limit: float = Field(gt=0, allow_inf_nan=False)
    attachment: float = Field(ge=0, allow_inf_nan=False)
    deposit_premium: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    rol: float | None = Field(default=None, ge=0, le=5)
    written_line: float = Field(default=1.0, ge=0, le=1)
    signed_line: float = Field(default=1.0, ge=0, le=1)
    reinstatements: float = Field(default=1.0, ge=0)
    confidence: float = Field(default=0.7, ge=0, le=1)


class IntakeLossHistoryRow(CamelModel):
    year: int | None = None
    occurrence_date: str | None = None
    event_name: str
    ground_up_loss_m: float = Field(ge=0, allow_inf_nan=False)
    currency: str = "USD"
    region_peril_key: str | None = None
    note: str = ""


class IntakeExtractedData(CamelModel):
    cedent_name: str = ""
    programme_name: str = ""
    broker: str = "Direct"
    inception_date: str | None = None
    expiry_date: str | None = None
    treaty_year: int = 2027
    renewal_period: str = "J27"
    currency: str = "USD"
    region_perils: list[str] = []
    perils: list[str] = []
    layers: list[IntakeLayerDraft] = []
    loss_history: list[IntakeLossHistoryRow] = []
    field_confidence: dict[str, float] = {}
    warnings: list[str] = []
    raw_excerpt: str = ""


class SubmissionDraftOut(CamelModel):
    draft_id: str
    door: IntakeDoor
    status: IntakeDraftStatus
    target_programme_id: str | None = None
    target_programme_label: str | None = None
    confirmed_programme_id: str | None = None
    source_filename: str
    source_file_type: IntakeFileKind
    source_ref: str = ""
    source_hash: str = ""
    sim_set_id: str | None = None
    sim_set_name: str | None = None
    loss_files: list[LossFileRecord] = []
    overall_confidence: float = Field(ge=0, le=1)
    extracted: IntakeExtractedData
    graph: GraphSpec
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None


class SubmissionDraftUpdate(CamelModel):
    target_programme_id: str | None = None
    sim_set_id: str | None = None
    extracted: IntakeExtractedData | None = None
    graph: GraphSpec | None = None


class SubmissionConfirmRequest(CamelModel):
    mode: Literal["CREATE_NEW", "ATTACH_EXISTING"] | None = None
    target_programme_id: str | None = None
    extracted: IntakeExtractedData | None = None
    graph: GraphSpec | None = None


class SubmissionConfirmResponse(CamelModel):
    draft: SubmissionDraftOut
    contract: ContractFull
    action: Literal["CREATED_DRAFT_PROGRAMME", "UPDATED_PROGRAMME"]
    manifest: RunManifest


class EmailAttachmentInput(CamelModel):
    filename: str
    content_base64: str
    content_type: str | None = None


class EmailMessageInput(CamelModel):
    message_id: str | None = None
    sender: str
    subject: str
    body_text: str = ""
    attachments: list[EmailAttachmentInput] = []


class EmailDoorStatus(CamelModel):
    connected: bool
    inbox_address: str | None = None
    provider: str = "NONE"
    message: str


class EmailPollResult(CamelModel):
    connected: bool
    inbox_address: str | None = None
    processed_count: int = 0
    skipped_count: int = 0
    drafts: list[SubmissionDraftOut] = []


class WatchedProgrammeFolder(CamelModel):
    programme_id: str
    cedent_name: str
    programme_name: str
    watched_folder: str
    resolved_exists: bool
    pending_files: int = 0


class FolderDoorStatus(CamelModel):
    root_dir: str
    exists: bool
    watched_programmes: list[WatchedProgrammeFolder] = []


class FolderScanResult(CamelModel):
    root_dir: str
    scanned_programmes: int = 0
    picked_up_count: int = 0
    skipped_count: int = 0
    drafts: list[SubmissionDraftOut] = []


class ProgrammeWatchedFolderUpdate(CamelModel):
    watched_folder: str | None = None


class IntakeStatusResponse(CamelModel):
    email: EmailDoorStatus
    folder: FolderDoorStatus
    pending_count: int = 0
    max_upload_mb: int = 25
