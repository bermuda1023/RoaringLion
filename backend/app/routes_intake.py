"""FastAPI router for the unified Submission Intake Pipeline (Drop-in, Email, Folder)."""

from __future__ import annotations

import hmac
import os
from typing import Callable

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError

from app.models.intake import (
    EmailDoorStatus,
    EmailMessageInput,
    EmailPollResult,
    FolderDoorStatus,
    FolderScanResult,
    IntakeDoor,
    IntakeDraftStatus,
    IntakeStatusResponse,
    ProgrammeWatchedFolderUpdate,
    SubmissionConfirmRequest,
    SubmissionConfirmResponse,
    SubmissionDraftOut,
    SubmissionDraftUpdate,
)
from app.models.phase1 import ContractFull
from app.services.contracts import ContractStore
from app.services.intake import (
    DraftValidationError,
    PayloadTooLargeError,
    SubmissionIntakeService,
    _max_base64_chars,
)
import app.services.intake as intake_service_mod
from app.services.intake_parsers import MAX_UPLOAD_BYTES, MAX_UPLOAD_MB
from app.services.lineage import LineageRegistry
from app.services.sim_sets import SimSetStore

CHUNK_SIZE = 256 * 1024  # 256 KB bounded streaming chunks


async def _read_request_body_bounded(request: Request, max_bytes: int) -> bytes:
    """Reject oversized JSON bodies on Content-Length or while streaming before Pydantic parses them."""
    cl_header = request.headers.get("content-length")
    if cl_header is not None:
        try:
            if int(cl_header) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Email ingest payload exceeds the {max_bytes} byte request limit.",
                )
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Email ingest payload exceeds the {max_bytes} byte request limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_upload_bounded(upload: UploadFile) -> bytes:
    """Read an UploadFile in bounded chunks to protect the 512 MB Render memory budget."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Upload '{upload.filename}' exceeds the {MAX_UPLOAD_MB} MB intake size limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def create_intake_router(
    *,
    contract_store: ContractStore,
    sim_store: SimSetStore,
    lineage: LineageRegistry,
    base_sim_id: str,
    on_cms_mutated: Callable[[], None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/intake", tags=["submission-intake"])
    svc = SubmissionIntakeService(
        contract_store=contract_store,
        sim_store=sim_store,
        lineage=lineage,
        base_sim_id=base_sim_id,
        on_cms_mutated=on_cms_mutated,
    )

    @router.get("/status", response_model=IntakeStatusResponse)
    def intake_status() -> IntakeStatusResponse:
        return svc.get_status()

    # ── Door 1: Drop-in ──

    @router.post("/drop-in", response_model=SubmissionDraftOut)
    async def drop_in_upload(
        file: UploadFile = File(...),
        programme_id: str | None = Form(default=None),
        attach_sim_set_id: str | None = Form(default=None),
    ) -> SubmissionDraftOut:
        raw = await _read_upload_bounded(file)
        if not raw:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        try:
            return svc.ingest_file(
                raw_bytes=raw,
                filename=file.filename or "submission.bin",
                door=IntakeDoor.DROP_IN,
                source_ref=f"Drop-in: {file.filename or 'submission.bin'}",
                target_programme_id=programme_id or None,
                attach_sim_set_id=attach_sim_set_id or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # ── Door 2: Email ──

    @router.get("/email/status", response_model=EmailDoorStatus)
    def email_status() -> EmailDoorStatus:
        return svc.get_email_status()

    @router.post("/email/ingest", response_model=list[SubmissionDraftOut])
    async def email_ingest_message(request: Request) -> list[SubmissionDraftOut]:
        """Ingest an email message (live webhook or fixture test message) through the unified pipeline."""
        max_req_bytes = _max_base64_chars(intake_service_mod.MAX_UPLOAD_BYTES) + 256 * 1024
        raw_body = await _read_request_body_bounded(request, max_req_bytes)
        try:
            msg = EmailMessageInput.model_validate_json(raw_body)
        except ValidationError as exc:
            if any(err.get("type") in ("too_long", "string_too_long") for err in exc.errors()):
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        try:
            return svc.ingest_email_message(msg)
        except PayloadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/email/poll", response_model=EmailPollResult)
    def email_poll(
        x_intake_poll_secret: str | None = Header(default=None, alias="X-Intake-Poll-Secret"),
    ) -> EmailPollResult:
        expected = os.getenv("ROARING_LION_INTAKE_POLL_SECRET", "").strip()
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="Email poll is disabled: ROARING_LION_INTAKE_POLL_SECRET is not configured on the server.",
            )
        provided = (x_intake_poll_secret or "").strip()
        if not provided or not hmac.compare_digest(provided, expected):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: missing or invalid X-Intake-Poll-Secret header.",
            )
        try:
            return svc.poll_email_inbox()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Email poll failed: {exc}") from exc

    # ── Door 3: Watched Folder ──

    @router.get("/folder/status", response_model=FolderDoorStatus)
    def folder_status() -> FolderDoorStatus:
        return svc.get_folder_status()

    @router.put("/programmes/{programme_id}/watched-folder", response_model=ContractFull)
    def set_watched_folder(programme_id: str, req: ProgrammeWatchedFolderUpdate) -> ContractFull:
        try:
            return svc.set_programme_watched_folder(programme_id, req.watched_folder)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/folder/scan", response_model=FolderScanResult)
    def folder_scan(programme_id: str | None = Query(default=None, alias="programmeId")) -> FolderScanResult:
        return svc.scan_watched_folders(programme_id=programme_id)

    # ── Drafts Queue, Review, Confirm & Discard ──

    @router.get("/drafts", response_model=list[SubmissionDraftOut])
    def list_drafts(
        status: IntakeDraftStatus | None = Query(default=None),
        programme_id: str | None = Query(default=None, alias="programmeId"),
        door: IntakeDoor | None = Query(default=None),
    ) -> list[SubmissionDraftOut]:
        return svc.list_drafts(status=status, programme_id=programme_id, door=door)

    @router.get("/drafts/{draft_id}", response_model=SubmissionDraftOut)
    def get_draft(draft_id: str) -> SubmissionDraftOut:
        d = svc.get_draft(draft_id)
        if not d:
            raise HTTPException(status_code=404, detail="Submission draft not found")
        return d

    @router.patch("/drafts/{draft_id}", response_model=SubmissionDraftOut)
    def update_draft(draft_id: str, patch: SubmissionDraftUpdate) -> SubmissionDraftOut:
        try:
            d = svc.update_draft(draft_id, patch)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not d:
            raise HTTPException(status_code=404, detail="Submission draft not found")
        return d

    @router.post("/drafts/{draft_id}/confirm", response_model=SubmissionConfirmResponse)
    def confirm_draft(draft_id: str, req: SubmissionConfirmRequest | None = None) -> SubmissionConfirmResponse:
        try:
            return svc.confirm_draft(draft_id, req)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DraftValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/drafts/{draft_id}/discard", response_model=SubmissionDraftOut)
    def discard_draft(draft_id: str) -> SubmissionDraftOut:
        try:
            d = svc.discard_draft(draft_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not d:
            raise HTTPException(status_code=404, detail="Submission draft not found")
        return d

    return router
