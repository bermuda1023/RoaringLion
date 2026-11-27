"""Unified Submission Intake Service — three doors (Drop-in, Email, Folder), one draft pipeline.

Draft only: nothing enters CMS (`contracts`) until an explicit Confirm call.
Confirm creates a `SUBMISSION` draft programme (or attaches to an existing programme
without changing its status) and never binds a live book.
"""

from __future__ import annotations

import base64
import email
import gc
import hashlib
import imaplib
import json
import logging
import os
import re
import ssl
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy.exc import IntegrityError

from app.db.database import get_data_dir, get_session
from app.db.models import ContractNoteRecord, SubmissionDraftRecord, WatchedIngestionRecord
from app.models.domain import BreadcrumbStep, GraphEdgeSpec, GraphNodeSpec, GraphSpec
from app.models.enums import NodeType, PerilCode, ProgrammeStatus
from app.models.intake import (
    EmailAttachmentInput,
    EmailDoorStatus,
    EmailMessageInput,
    EmailPollResult,
    FolderDoorStatus,
    FolderScanResult,
    IntakeDoor,
    IntakeDraftStatus,
    IntakeExtractedData,
    IntakeFileKind,
    IntakeLayerDraft,
    IntakeStatusResponse,
    SubmissionConfirmRequest,
    SubmissionConfirmResponse,
    SubmissionDraftOut,
    SubmissionDraftUpdate,
    WatchedProgrammeFolder,
)
from app.models.phase1 import (
    ContractCreate,
    ContractFull,
    ContractLayerSpec,
    ContractUpdate,
    EltIngestRequest,
    LossFileRecord,
)
from app.services.contracts import ContractStore, ensure_layer_codes, relabel_graph
from app.services.intake_parsers import MAX_UPLOAD_BYTES, MAX_UPLOAD_MB, parse_submission_bytes
from app.services.lineage import LineageRegistry, canonical_hash
from app.services.sim_sets import SimSetStore

SUPPORTED_INTAKE_EXTS = {".pdf", ".xlsx", ".xlsm", ".csv", ".parquet", ".txt"}
MAX_EMAIL_ATTACHMENTS = int(os.getenv("ROARING_LION_INTAKE_MAX_ATTACHMENTS", "10"))

logger = logging.getLogger(__name__)


class DraftValidationError(ValueError):
    """Raised when a draft fails validation on confirm (maps to HTTP 422)."""


class PayloadTooLargeError(ValueError):
    """Raised when an intake payload exceeds bounded size or attachment-count limits (maps to HTTP 413)."""


def _max_base64_chars(max_bytes: int) -> int:
    return ((max_bytes + 2) // 3) * 4


def _max_imap_message_bytes() -> int:
    """Maximum raw RFC822 message size to download via IMAP (base64 overhead + MIME headers)."""
    return _max_base64_chars(MAX_UPLOAD_BYTES) + 256 * 1024


def _parse_rfc822_size(size_data: object) -> int | None:
    if not isinstance(size_data, list):
        return None
    for item in size_data:
        raw = item[0] if isinstance(item, tuple) else item
        text = raw.decode("ascii", errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
        m = re.search(r"RFC822\.SIZE\s+(\d+)", text, re.I)
        if m:
            return int(m.group(1))
    return None


def _layer_spec_from_draft(
    lyr: IntakeLayerDraft,
    *,
    layer_code: str | None,
    fallback_name: str,
) -> ContractLayerSpec:
    """Build a ContractLayerSpec where confirmed deposit_premium wins over stale ROL."""
    if lyr.deposit_premium > 0:
        # Omit rol so _layer_node keeps deposit_premium exact and derives ROL = deposit_premium / limit.
        return ContractLayerSpec(
            layer_code=layer_code,
            name=lyr.name or fallback_name,
            layer_type=lyr.layer_type,
            limit=lyr.limit,
            attachment=lyr.attachment,
            deposit_premium=lyr.deposit_premium,
            rol=None,
            reinstatements=lyr.reinstatements,
            written_line=lyr.written_line,
            signed_line=lyr.signed_line,
        )
    return ContractLayerSpec(
        layer_code=layer_code,
        name=lyr.name or fallback_name,
        layer_type=lyr.layer_type,
        limit=lyr.limit,
        attachment=lyr.attachment,
        deposit_premium=0.0,
        rol=lyr.rol,
        reinstatements=lyr.reinstatements,
        written_line=lyr.written_line,
        signed_line=lyr.signed_line,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_submissions_folder_root() -> Path:
    env_root = os.getenv("ROARING_LION_SUBMISSIONS_FOLDER_ROOT", "").strip()
    root = Path(env_root) if env_root else (get_data_dir() / "submissions_watch")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_safe_watched_folder(raw_folder: str) -> tuple[str, Path]:
    """Validate and resolve a programme's watched folder inside the configured root."""
    cleaned = raw_folder.strip().replace("\\", "/").strip("/")
    if not cleaned:
        raise ValueError("Watched folder path cannot be empty.")
    if cleaned.startswith("/") or ".." in Path(cleaned).parts:
        raise ValueError("Watched folder must be a relative path inside the configured submissions root.")
    root = get_submissions_folder_root()
    resolved = (root / cleaned).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Watched folder escapes the configured submissions root.") from exc
    return cleaned, resolved


def _valid_peril_codes(raw_perils: list[str], region_perils: list[str]) -> list[PerilCode]:
    valid = {p.value for p in PerilCode}
    out: list[PerilCode] = []
    for p in raw_perils:
        up = str(p).strip().upper()
        if up in valid and PerilCode(up) not in out:
            out.append(PerilCode(up))
    for rp in region_perils:
        if "-" in rp:
            up = rp.rsplit("-", 1)[-1].upper()
            if up in valid and PerilCode(up) not in out:
                out.append(PerilCode(up))
    return out or [PerilCode.WS]


class SubmissionIntakeService:
    """Single intake pipeline backing Drop-in, Email, and Watched Folder doors."""

    def __init__(
        self,
        *,
        contract_store: ContractStore,
        sim_store: SimSetStore,
        lineage: LineageRegistry,
        base_sim_id: str,
        on_cms_mutated: Callable[[], None] | None = None,
    ) -> None:
        self.contract_store = contract_store
        self.sim_store = sim_store
        self.lineage = lineage
        self.base_sim_id = base_sim_id
        self.on_cms_mutated = on_cms_mutated

    # ─────────────────────────── Helpers ───────────────────────────

    def _sim_set_metadata(self, sim_set_id: str | None) -> tuple[str | None, list[LossFileRecord]]:
        """Look up sim-set metadata from SQLite without loading Parquet arrays into RAM."""
        if not sim_set_id:
            return None, []
        for s in self.sim_store.list_sim_sets():
            if s.sim_set_id == sim_set_id:
                return s.name, list(s.loss_files)
        return None, []

    def sketch_draft_graph(
        self,
        *,
        draft_id: str,
        extracted: IntakeExtractedData,
        sim_set_id: str,
        loss_files: list[LossFileRecord],
        target_programme_id: str | None = None,
    ) -> GraphSpec:
        """Sketch an editable structuring DAG: Loss Source -> Region/Peril Filter -> Layers -> Pricing Outputs + YELT."""
        prog_ref = target_programme_id or f"draft-{draft_id}"
        src_id = f"src-{draft_id}"
        flt_id = f"flt-{draft_id}"
        out_id = f"out-{draft_id}"

        loss_file_dicts = [lf.model_dump(by_alias=True) for lf in loss_files]
        available_rps = {lf.region_peril for lf in loss_files}
        filtered_rps = [rp for rp in extracted.region_perils if (not available_rps or rp in available_rps)]

        nodes: list[GraphNodeSpec] = [
            GraphNodeSpec(
                node_id=src_id,
                node_type=NodeType.LOSS_SOURCE,
                label=f"Loss Source ({sim_set_id})",
                params={"simSetId": sim_set_id, "lossFiles": loss_file_dicts, "labelName": "Loss Source"},
                position={"x": 40, "y": 100},
            ),
            GraphNodeSpec(
                node_id=flt_id,
                node_type=NodeType.REGION_PERIL_FILTER,
                label=f"Zone Peril ({', '.join(filtered_rps)})" if filtered_rps else "Zone Peril — All zones & perils",
                params={"regionPerils": filtered_rps, "labelName": "Zone Peril"},
                position={"x": 300, "y": 100},
            ),
        ]
        edges: list[GraphEdgeSpec] = [
            GraphEdgeSpec(edge_id=f"e1-{draft_id}", source=src_id, target=flt_id, sign=1, weight=1.0),
        ]

        layers = extracted.layers or [
            IntakeLayerDraft(
                layer_code="L1",
                name="Draft Layer 1",
                layer_type="OCC_XL",
                limit=25.0,
                attachment=50.0,
                deposit_premium=4.0,
                rol=0.16,
                written_line=1.0,
                signed_line=1.0,
                confidence=0.35,
            )
        ]

        for i, lyr in enumerate(layers):
            lyr_id = f"lyr-{draft_id}-{i + 1}"
            px_id = f"px-{lyr_id}"
            y = 20 + i * 140
            rol_val = lyr.rol if lyr.rol is not None else (round(lyr.deposit_premium / lyr.limit, 4) if lyr.limit > 0 else 0.0)
            prem_val = lyr.deposit_premium if lyr.deposit_premium > 0 else round(lyr.limit * rol_val, 4)
            node_type = NodeType.AGG_XL if lyr.layer_type == "AGG_XL" else NodeType.OCC_XL
            label_name = lyr.name or f"Layer {i + 1}"

            nodes.append(
                GraphNodeSpec(
                    node_id=lyr_id,
                    node_type=node_type,
                    label=f"{lyr.layer_code} — {label_name}",
                    params={
                        "layerCode": lyr.layer_code or f"L{i + 1}",
                        "labelName": label_name,
                        "limit": lyr.limit,
                        "attachment": lyr.attachment,
                        "reinstatements": lyr.reinstatements,
                        "ripRate": 1.0,
                        "depositPremium": prem_val,
                        "rol": rol_val,
                        "writtenLine": lyr.written_line,
                        "signedLine": lyr.signed_line,
                        "brokeragePct": 0.10,
                    },
                    position={"x": 600, "y": y},
                )
            )
            nodes.append(
                GraphNodeSpec(
                    node_id=px_id,
                    node_type=NodeType.PRICING_OUTPUT,
                    label=f"Pricing — {label_name}",
                    params={
                        "contractId": prog_ref,
                        "layerNodeId": lyr_id,
                        "labelName": f"Pricing — {label_name}",
                    },
                    position={"x": 920, "y": y},
                )
            )
            edges.extend([
                GraphEdgeSpec(edge_id=f"e-in-{lyr_id}", source=flt_id, target=lyr_id, sign=1, weight=1.0),
                GraphEdgeSpec(edge_id=f"e-px-{lyr_id}", source=lyr_id, target=px_id, sign=1, weight=1.0),
                GraphEdgeSpec(edge_id=f"e-out-{lyr_id}", source=lyr_id, target=out_id, sign=1, weight=1.0),
            ])

        nodes.append(
            GraphNodeSpec(
                node_id=out_id,
                node_type=NodeType.YELT_OUTPUT,
                label="Programme YELT (Draft)",
                params={"view": "ADJUSTED"},
                position={"x": 1220, "y": 20 + max(0, len(layers) - 1) * 70},
            )
        )

        graph = GraphSpec(
            graph_id=f"grp-{draft_id}",
            name=f"{extracted.cedent_name or 'Draft'} — {extracted.programme_name or 'Submission'}",
            version=1,
            currency=extracted.currency or "USD",
            nodes=nodes,
            edges=edges,
            content_hash="",
        )
        ensure_layer_codes(graph, strict=False)
        relabel_graph(graph)
        graph.content_hash = canonical_hash({
            "nodes": [n.model_dump() for n in graph.nodes],
            "edges": [e.model_dump() for e in graph.edges],
        })
        return graph

    def match_existing_programme(self, *texts: str) -> ContractFull | None:
        """Match an incoming submission (subject, filename, extracted cedent/programme) to an existing CMS programme."""
        haystack = " ".join(t for t in texts if t).lower()
        if not haystack.strip():
            return None
        contracts = self.contract_store.list_contracts()

        # 1. Exact programmeId or uniqueCode match
        for c in contracts:
            if c.programme_id.lower() in haystack:
                return c
            if c.profile and c.profile.unique_code and c.profile.unique_code.lower() in haystack:
                return c

        # 2. Cedent + programme name scoring
        def _tokens(s: str) -> set[str]:
            stop = {"the", "inc", "corp", "ltd", "llc", "insurance", "reinsurance", "cat", "xl", "draft", "submission", "pdf", "xlsx", "csv"}
            return {w for w in re.findall(r"[a-z0-9]{3,}", s.lower()) if w not in stop}

        hay_tokens = _tokens(haystack)
        best: tuple[float, ContractFull | None] = (0.0, None)
        for c in contracts:
            ced_lower = c.cedent_name.lower()
            prog_lower = c.programme_name.lower()
            score = 0.0
            if ced_lower and len(ced_lower) >= 4 and ced_lower in haystack:
                score += 3.0
            if prog_lower and len(prog_lower) >= 4 and prog_lower in haystack:
                score += 4.0
            ced_toks = _tokens(c.cedent_name)
            prog_toks = _tokens(c.programme_name)
            if ced_toks:
                score += 2.0 * (len(ced_toks & hay_tokens) / len(ced_toks))
            if prog_toks:
                score += 2.0 * (len(prog_toks & hay_tokens) / len(prog_toks))
            if score > best[0]:
                best = (score, c)

        return best[1] if best[0] >= 3.0 else None

    def _record_to_out(
        self,
        rec: SubmissionDraftRecord,
        *,
        contracts_by_id: dict[str, ContractFull] | None = None,
        sim_sets_by_id: dict[str, tuple[str, list[LossFileRecord]]] | None = None,
    ) -> SubmissionDraftOut:
        extracted = IntakeExtractedData(**json.loads(rec.extracted_json or "{}"))
        graph = GraphSpec(**json.loads(rec.graph_json or "{}"))
        target_label = None
        if rec.target_programme_id:
            if contracts_by_id is not None:
                c = contracts_by_id.get(rec.target_programme_id)
            else:
                c = self.contract_store.get_contract(rec.target_programme_id)
            if c:
                target_label = f"{c.cedent_name} — {c.programme_name}"

        sim_name: str | None = None
        loss_files: list[LossFileRecord] = []
        if rec.sim_set_id:
            if sim_sets_by_id is not None and rec.sim_set_id in sim_sets_by_id:
                sim_name, loss_files = sim_sets_by_id[rec.sim_set_id]
            else:
                sim_name, loss_files = self._sim_set_metadata(rec.sim_set_id)

        return SubmissionDraftOut(
            draft_id=rec.draft_id,
            door=IntakeDoor(rec.door),
            status=IntakeDraftStatus(rec.status),
            target_programme_id=rec.target_programme_id,
            target_programme_label=target_label,
            confirmed_programme_id=rec.confirmed_programme_id,
            source_filename=rec.source_filename,
            source_file_type=IntakeFileKind(rec.source_file_type),
            source_ref=rec.source_ref,
            source_hash=rec.source_hash,
            sim_set_id=rec.sim_set_id,
            sim_set_name=sim_name,
            loss_files=loss_files,
            overall_confidence=rec.overall_confidence,
            extracted=extracted,
            graph=graph,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
            decided_at=rec.decided_at,
        )

    # ─────────────────────────── Core Single Pipeline ───────────────────────────

    def _get_draft_by_dedup_key(self, external_key: str) -> SubmissionDraftOut | None:
        with get_session() as db:
            watch = (
                db.query(WatchedIngestionRecord)
                .filter(WatchedIngestionRecord.external_key == external_key)
                .first()
            )
            if not watch:
                return None
            rec = db.get(SubmissionDraftRecord, watch.draft_id)
            return self._record_to_out(rec) if rec else None

    def ingest_file(
        self,
        *,
        raw_bytes: bytes,
        filename: str,
        door: IntakeDoor,
        source_ref: str = "",
        target_programme_id: str | None = None,
        hint_subject: str = "",
        attach_sim_set_id: str | None = None,
        dedup_key: str | None = None,
    ) -> SubmissionDraftOut:
        """Process one submission file through the unified pipeline and save a PENDING_REVIEW draft.

        When `dedup_key` is supplied, the dedup marker and draft are committed in the same
        database transaction; retrying the same key returns the existing draft without
        creating duplicates.
        """
        if dedup_key:
            existing = self._get_draft_by_dedup_key(dedup_key)
            if existing is not None:
                return existing

        if len(raw_bytes) > MAX_UPLOAD_BYTES:
            raise ValueError(f"File '{filename}' exceeds the {MAX_UPLOAD_MB} MB intake limit.")

        source_hash = hashlib.sha256(raw_bytes).hexdigest()[:24]
        parsed = parse_submission_bytes(raw_bytes, filename=filename, hint_subject=hint_subject)
        draft_id = f"sub-{uuid.uuid4().hex[:10]}"

        sim_set_id = attach_sim_set_id
        loss_files: list[LossFileRecord] = []
        extracted = parsed.extracted
        overall_conf = parsed.overall_confidence

        # YELT / ELT modelling files skip any LLM and go directly through existing SimSetStore ingest
        if parsed.file_kind == IntakeFileKind.YELT and parsed.yelt_spec is not None:
            spec = parsed.yelt_spec
            sim_detail = self.sim_store.ingest_yelt_bytes(
                raw_bytes=raw_bytes,
                filename=filename,
                name=f"Submission YELT — {Path(filename).stem}",
                event_catalog_id="SUBMISSION_YELT",
                num_trials=spec.num_trials,
                currency=spec.currency,
                trial_col=spec.trial_col,
                event_col=spec.event_col,
                loss_col=spec.loss_col,
                day_col=spec.day_col,
                region_peril_col=spec.region_peril_col,
                default_region_peril=spec.default_region_peril,
                is_industry_catalog=False,
                lineage=self.lineage,
            )
            sim_set_id = sim_detail.sim_set_id
            loss_files = list(sim_detail.loss_files)
            extracted = IntakeExtractedData(
                cedent_name="",
                programme_name=f"Loss Source Upload — {Path(filename).stem}",
                currency=sim_detail.currency,
                region_perils=list(sim_detail.region_perils),
                perils=[p.value for p in _valid_peril_codes([], sim_detail.region_perils)],
                field_confidence={"lossSource": 0.99, "regionPerils": 0.98},
                raw_excerpt=f"Ingested YELT '{filename}' ({sim_detail.total_events} events, {sim_detail.num_trials} trials, AAL {sim_detail.expected_annual_loss})",
            )
            overall_conf = 0.98

        elif parsed.file_kind == IntakeFileKind.ELT and parsed.elt_spec is not None:
            spec = parsed.elt_spec
            sim_detail = self.sim_store.ingest_elt(
                EltIngestRequest(
                    name=f"Submission ELT — {Path(filename).stem}",
                    event_catalog_id="SUBMISSION_ELT",
                    num_trials=spec.num_trials,
                    currency=spec.currency,
                    rows=spec.rows,
                ),
                self.lineage,
            )
            sim_set_id = sim_detail.sim_set_id
            loss_files = list(sim_detail.loss_files)
            extracted = IntakeExtractedData(
                cedent_name="",
                programme_name=f"ELT Loss Source — {Path(filename).stem}",
                currency=sim_detail.currency,
                region_perils=list(sim_detail.region_perils),
                perils=[p.value for p in _valid_peril_codes([], sim_detail.region_perils)],
                field_confidence={"lossSource": 0.97, "regionPerils": 0.96},
                raw_excerpt=f"Ingested ELT '{filename}' ({len(spec.rows)} ELT events -> {sim_detail.total_events} simulated events, AAL {sim_detail.expected_annual_loss})",
            )
            overall_conf = 0.96

        # Resolve target programme (explicit or matched from cedent/programme/subject)
        matched_contract: ContractFull | None = None
        if target_programme_id:
            matched_contract = self.contract_store.get_contract(target_programme_id)
        elif door in (IntakeDoor.EMAIL, IntakeDoor.FOLDER):
            matched_contract = self.match_existing_programme(
                hint_subject,
                filename,
                extracted.cedent_name,
                extracted.programme_name,
            )
            if matched_contract:
                target_programme_id = matched_contract.programme_id

        if matched_contract and not extracted.cedent_name:
            extracted.cedent_name = matched_contract.cedent_name
        if matched_contract and (parsed.file_kind in (IntakeFileKind.YELT, IntakeFileKind.ELT)):
            extracted.programme_name = matched_contract.programme_name
            extracted.inception_date = matched_contract.inception_date
            extracted.expiry_date = matched_contract.expiry_date
            extracted.treaty_year = matched_contract.treaty_year
            extracted.renewal_period = matched_contract.renewal_period
        elif not extracted.cedent_name:
            extracted.cedent_name = f"{Path(filename).stem.replace('_', ' ').title()} Cedent (Draft)"

        # Attach loss source sim_set_id if not already set from a YELT/ELT upload
        if not sim_set_id:
            sim_set_id = matched_contract.sim_set_id if matched_contract else self.base_sim_id
        if not loss_files:
            _, loss_files = self._sim_set_metadata(sim_set_id)

        available_rps = {lf.region_peril for lf in loss_files}
        missing_rps = [rp for rp in extracted.region_perils if available_rps and rp not in available_rps]
        if missing_rps:
            extracted.warnings.append(
                f"Region/peril key(s) {', '.join(missing_rps)} are not in loss source '{sim_set_id}' "
                f"(available: {', '.join(sorted(available_rps))})."
            )

        graph = self.sketch_draft_graph(
            draft_id=draft_id,
            extracted=extracted,
            sim_set_id=sim_set_id,
            loss_files=loss_files,
            target_programme_id=target_programme_id,
        )

        rec = SubmissionDraftRecord(
            draft_id=draft_id,
            door=door.value,
            status=IntakeDraftStatus.PENDING_REVIEW.value,
            target_programme_id=target_programme_id,
            confirmed_programme_id=None,
            source_filename=filename,
            source_file_type=parsed.file_kind.value,
            source_ref=source_ref or filename,
            source_hash=source_hash,
            sim_set_id=sim_set_id,
            overall_confidence=overall_conf,
            extracted_json=extracted.model_dump_json(),
            graph_json=graph.model_dump_json(),
        )
        with get_session() as db:
            try:
                db.add(rec)
                if dedup_key:
                    db.add(
                        WatchedIngestionRecord(
                            door=door.value,
                            programme_id=target_programme_id,
                            external_key=dedup_key,
                            draft_id=draft_id,
                        )
                    )
                db.commit()
                db.refresh(rec)
            except IntegrityError:
                db.rollback()
                if dedup_key:
                    existing = self._get_draft_by_dedup_key(dedup_key)
                    if existing is not None:
                        return existing
                raise

        gc.collect()
        return self._record_to_out(rec)

    # ─────────────────────────── Door 2: Email ───────────────────────────

    def get_email_status(self) -> EmailDoorStatus:
        inbox_address = os.getenv("ROARING_LION_SUBMISSIONS_EMAIL", "").strip() or None
        host = os.getenv("ROARING_LION_EMAIL_IMAP_HOST", "").strip()
        user = os.getenv("ROARING_LION_EMAIL_USERNAME", "").strip()
        password = os.getenv("ROARING_LION_EMAIL_PASSWORD", "").strip()
        connected = bool(host and user and password)
        if connected:
            return EmailDoorStatus(
                connected=True,
                inbox_address=inbox_address or user,
                provider="IMAP",
                message=f"Connected to {inbox_address or user} via IMAP ({host}).",
            )
        return EmailDoorStatus(
            connected=False,
            inbox_address=inbox_address,
            provider="NONE",
            message=(
                f"Submissions inbox ({inbox_address}) is not connected — IMAP credentials not configured."
                if inbox_address
                else "Submissions email inbox is not connected. Configure ROARING_LION_SUBMISSIONS_EMAIL and IMAP credentials, or test with fixture messages."
            ),
        )

    def _existing_email_attachment_drafts(self, message_id: str | None) -> list[SubmissionDraftRecord]:
        if not message_id:
            return []
        with get_session() as db:
            watch_rows = (
                db.query(WatchedIngestionRecord)
                .filter(WatchedIngestionRecord.external_key.like(f"email:{message_id}:att:%"))
                .all()
            )
            draft_ids = [w.draft_id for w in watch_rows]
            if not draft_ids:
                return []
            return (
                db.query(SubmissionDraftRecord)
                .filter(SubmissionDraftRecord.draft_id.in_(draft_ids))
                .all()
            )

    def _ingest_email_parts(
        self,
        *,
        message_id: str | None,
        sender: str,
        subject: str,
        body_text: str,
        raw_attachments: list[tuple[int, str, bytes]],
        rejected_attachments: list[str] | None = None,
        has_existing_attachments: bool = False,
    ) -> list[SubmissionDraftOut]:
        """Ingest decoded email attachments (or body text) through the unified pipeline."""
        attachment_names = " ".join(fname for _, fname, _ in raw_attachments)
        matched = self.match_existing_programme(subject, body_text, attachment_names)
        target_prog_id = matched.programme_id if matched else None
        source_ref = f"Email from {sender}: {subject}"

        drafts: list[SubmissionDraftOut] = []

        if raw_attachments:
            # First ingest any modelling attachments so their sim_set_id can attach to sibling PDF/Excel drafts.
            # Also inspect already-stored sibling drafts from earlier polls of the same message_id.
            modelling_exts = {".csv", ".parquet"}
            existing_siblings = self._existing_email_attachment_drafts(message_id)
            attached_sim_id: str | None = next(
                (
                    s.sim_set_id
                    for s in existing_siblings
                    if s.source_file_type in (IntakeFileKind.YELT.value, IntakeFileKind.ELT.value) and s.sim_set_id
                ),
                None,
            )
            doc_items: list[tuple[int, str, bytes]] = []
            modelling_drafts: list[SubmissionDraftOut] = []

            for idx, fname, raw in raw_attachments:
                ext = Path(fname).suffix.lower()
                item_key = f"email:{message_id}:att:{idx}:{fname}" if message_id else None
                if ext in modelling_exts:
                    d = self.ingest_file(
                        raw_bytes=raw,
                        filename=fname,
                        door=IntakeDoor.EMAIL,
                        source_ref=source_ref,
                        target_programme_id=target_prog_id,
                        hint_subject=f"{subject}\n{body_text}",
                        dedup_key=item_key,
                    )
                    if d.sim_set_id:
                        attached_sim_id = d.sim_set_id
                    modelling_drafts.append(d)
                else:
                    doc_items.append((idx, fname, raw))

            # Reverse cross-poll link: if a slip landed on an earlier poll and its YELT/ELT arrived now,
            # attach the new sim_set_id and re-sketch the earlier pending slip draft's graph.
            if attached_sim_id and existing_siblings:
                _, loss_files = self._sim_set_metadata(attached_sim_id)
                with get_session() as db:
                    for sib in existing_siblings:
                        if (
                            sib.status == IntakeDraftStatus.PENDING_REVIEW.value
                            and sib.source_file_type not in (IntakeFileKind.YELT.value, IntakeFileKind.ELT.value)
                            and sib.sim_set_id != attached_sim_id
                        ):
                            rec = db.get(SubmissionDraftRecord, sib.draft_id)
                            if rec is not None:
                                ext_data = IntakeExtractedData(**json.loads(rec.extracted_json or "{}"))
                                rec.sim_set_id = attached_sim_id
                                graph = self.sketch_draft_graph(
                                    draft_id=rec.draft_id,
                                    extracted=ext_data,
                                    sim_set_id=attached_sim_id,
                                    loss_files=loss_files,
                                    target_programme_id=rec.target_programme_id,
                                )
                                rec.graph_json = graph.model_dump_json()
                                rec.updated_at = _utcnow()
                    db.commit()

            for idx, fname, raw in doc_items:
                item_key = f"email:{message_id}:att:{idx}:{fname}" if message_id else None
                d = self.ingest_file(
                    raw_bytes=raw,
                    filename=fname,
                    door=IntakeDoor.EMAIL,
                    source_ref=source_ref,
                    target_programme_id=target_prog_id,
                    hint_subject=f"{subject}\n{body_text}",
                    attach_sim_set_id=attached_sim_id,
                    dedup_key=item_key,
                )
                drafts.append(d)

            if not doc_items:
                drafts.extend(modelling_drafts)
            else:
                drafts = modelling_drafts + drafts
        elif not has_existing_attachments:
            effective_body = body_text or subject
            if rejected_attachments:
                effective_body = (
                    f"{effective_body}\nRejected oversized attachment(s): {', '.join(rejected_attachments)}"
                ).strip()
            raw_body = effective_body.encode("utf-8")[:MAX_UPLOAD_BYTES]
            body_key = f"email:{message_id}:body" if message_id else None
            d = self.ingest_file(
                raw_bytes=raw_body,
                filename="email_body.txt",
                door=IntakeDoor.EMAIL,
                source_ref=source_ref,
                target_programme_id=target_prog_id,
                hint_subject=subject,
                dedup_key=body_key,
            )
            drafts.append(d)

        if rejected_attachments and drafts:
            note = f"Skipped oversized attachment(s) exceeding {MAX_UPLOAD_MB} MB limit: {', '.join(rejected_attachments)}"
            with get_session() as db:
                for d in drafts:
                    rec = db.get(SubmissionDraftRecord, d.draft_id)
                    if rec:
                        ext_data = IntakeExtractedData(**json.loads(rec.extracted_json or "{}"))
                        if note not in ext_data.warnings:
                            ext_data.warnings.append(note)
                            rec.extracted_json = ext_data.model_dump_json()
                db.commit()
            drafts = [self.get_draft(d.draft_id) or d for d in drafts]

        return drafts

    def ingest_email_message(self, msg: EmailMessageInput) -> list[SubmissionDraftOut]:
        """Process one email message (live or fixture): match programme and file drafts.

        Validates attachment count and encoded payload sizes BEFORE base64 decoding to avoid
        unbounded memory allocation. Each attachment/body draft commits its dedup key in the
        same transaction as the draft so retries return existing draft(s) without duplicates.
        """
        if len(msg.attachments) > MAX_EMAIL_ATTACHMENTS:
            raise PayloadTooLargeError(
                f"Email contains {len(msg.attachments)} attachments, exceeding the {MAX_EMAIL_ATTACHMENTS}-attachment limit."
            )

        max_b64 = _max_base64_chars(MAX_UPLOAD_BYTES)
        raw_attachments: list[tuple[int, str, bytes]] = []
        total_decoded = 0

        for idx, att in enumerate(msg.attachments):
            # Guard before base64 decoding so oversized payloads are never allocated.
            if len(att.content_base64) > max_b64 * 2:
                raise PayloadTooLargeError(
                    f"Attachment '{att.filename}' exceeds the {MAX_UPLOAD_MB} MB intake limit."
                )
            compact = "".join(att.content_base64.split())
            if len(compact) > max_b64:
                raise PayloadTooLargeError(
                    f"Attachment '{att.filename}' exceeds the {MAX_UPLOAD_MB} MB intake limit."
                )
            raw = base64.b64decode(compact, validate=True)
            if len(raw) > MAX_UPLOAD_BYTES:
                raise PayloadTooLargeError(
                    f"Attachment '{att.filename}' exceeds the {MAX_UPLOAD_MB} MB intake limit."
                )
            total_decoded += len(raw)
            if total_decoded > MAX_UPLOAD_BYTES:
                raise PayloadTooLargeError(
                    f"Total email attachment payload exceeds the {MAX_UPLOAD_MB} MB intake limit."
                )
            raw_attachments.append((idx, att.filename, raw))

        return self._ingest_email_parts(
            message_id=msg.message_id,
            sender=msg.sender,
            subject=msg.subject,
            body_text=msg.body_text,
            raw_attachments=raw_attachments,
        )

    def poll_email_inbox(self, max_messages: int = 20) -> EmailPollResult:
        """Poll configured IMAP inbox if credentials exist; otherwise report disconnected cleanly.

        Uses verified TLS (`ssl.create_default_context()`), fetches with `BODY.PEEK[]` so
        `\\Seen` is not set prematurely, skips oversized attachments while ingesting valid
        siblings (without base64 round-tripping), and marks `\\Seen` only after ingest succeeds.
        Transient parse/ingest errors leave the message `UNSEEN` for retry on the next poll.
        """
        status = self.get_email_status()
        if not status.connected:
            return EmailPollResult(
                connected=False,
                inbox_address=status.inbox_address,
                processed_count=0,
                skipped_count=0,
                drafts=[],
            )

        host = os.getenv("ROARING_LION_EMAIL_IMAP_HOST", "").strip()
        port = int(os.getenv("ROARING_LION_EMAIL_IMAP_PORT", "993"))
        user = os.getenv("ROARING_LION_EMAIL_USERNAME", "").strip()
        password = os.getenv("ROARING_LION_EMAIL_PASSWORD", "").strip()

        drafts: list[SubmissionDraftOut] = []
        processed = 0
        skipped = 0
        max_b64 = _max_base64_chars(MAX_UPLOAD_BYTES)
        ssl_ctx = ssl.create_default_context()

        with imaplib.IMAP4_SSL(host, port, ssl_context=ssl_ctx) as imap:
            imap.login(user, password)
            imap.select("INBOX")
            _, msg_nums = imap.search(None, "UNSEEN")
            ids = (msg_nums[0].split() if msg_nums and msg_nums[0] else [])[:max_messages]
            for num in ids:
                try:
                    # Check RFC822.SIZE before downloading the full message body
                    _, size_data = imap.fetch(num, "(RFC822.SIZE)")
                    msg_size = _parse_rfc822_size(size_data)
                    max_msg_bytes = _max_imap_message_bytes()
                    if msg_size is not None and msg_size > max_msg_bytes:
                        logger.warning(
                            "Skipping oversized IMAP message %s (%d bytes > %d byte cap) without downloading body",
                            num,
                            msg_size,
                            max_msg_bytes,
                        )
                        imap.store(num, "+FLAGS", "\\Seen")
                        skipped += 1
                        continue

                    _, data = imap.fetch(num, "(BODY.PEEK[])")
                    if not data or not isinstance(data[0], tuple):
                        skipped += 1
                        continue
                    parsed_mail = email.message_from_bytes(data[0][1])
                    msg_id = str(parsed_mail.get("Message-ID") or num.decode("utf-8", errors="ignore"))
                    sender = str(parsed_mail.get("From") or "unknown")
                    subject = str(parsed_mail.get("Subject") or "(No Subject)")
                    body_parts: list[str] = []
                    raw_attachments: list[tuple[int, str, bytes]] = []
                    rejected_attachments: list[str] = []
                    has_deferred_in_limit = False
                    already_stored_attachments = 0
                    total_bytes = 0
                    att_idx = 0

                    with get_session() as db:
                        already_ingested_keys = {
                            r.external_key
                            for r in db.query(WatchedIngestionRecord.external_key)
                            .filter(WatchedIngestionRecord.external_key.like(f"email:{msg_id}:%"))
                            .all()
                        }

                    for part in parsed_mail.walk():
                        disp = str(part.get("Content-Disposition") or "")
                        fname = part.get_filename()
                        if fname or "attachment" in disp.lower():
                            safe_name = fname or f"attachment_{att_idx}.bin"
                            item_key = f"email:{msg_id}:att:{att_idx}:{safe_name}"
                            if item_key in already_ingested_keys:
                                already_stored_attachments += 1
                                att_idx += 1
                                continue
                            # Check per-file encoded length before decoding to avoid allocating oversized buffers
                            raw_encoded = part.get_payload(decode=False)
                            if isinstance(raw_encoded, str) and len(raw_encoded) > max_b64 * 2:
                                logger.warning("Skipping oversized attachment '%s' on IMAP message %s", safe_name, num)
                                rejected_attachments.append(safe_name)
                                att_idx += 1
                                continue
                            payload = part.get_payload(decode=True)
                            if payload is None:
                                att_idx += 1
                                continue
                            if len(payload) > MAX_UPLOAD_BYTES:
                                logger.warning(
                                    "Skipping oversized attachment '%s' (%d bytes) on IMAP message %s",
                                    safe_name,
                                    len(payload),
                                    num,
                                )
                                rejected_attachments.append(safe_name)
                                att_idx += 1
                                continue
                            # In-limit attachment: if this poll's batch count or byte budget is full,
                            # defer it to the next poll and keep the message UNSEEN (never drop or mislabel it).
                            if len(raw_attachments) >= MAX_EMAIL_ATTACHMENTS or (total_bytes + len(payload)) > MAX_UPLOAD_BYTES:
                                has_deferred_in_limit = True
                                att_idx += 1
                                continue
                            total_bytes += len(payload)
                            raw_attachments.append((att_idx, safe_name, payload))
                            att_idx += 1
                        elif part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                body_parts.append(payload.decode("utf-8", errors="replace"))

                    if not raw_attachments and already_stored_attachments > 0:
                        # All attachments on this message are already stored (or deferred); never create a body draft.
                        created = []
                    elif not raw_attachments and f"email:{msg_id}:body" in already_ingested_keys:
                        created = []
                    else:
                        created = self._ingest_email_parts(
                            message_id=msg_id,
                            sender=sender,
                            subject=subject,
                            body_text="\n".join(body_parts),
                            raw_attachments=raw_attachments,
                            rejected_attachments=rejected_attachments,
                            has_existing_attachments=already_stored_attachments > 0,
                        )
                except Exception as exc:
                    # Leave message UNSEEN so transient failures are retried on the next poll.
                    logger.warning("Failed to ingest IMAP message %s; leaving UNSEEN for retry: %s", num, exc)
                    skipped += 1
                    continue

                # Only mark \Seen when every in-limit attachment has been stored (no deferred files left).
                if not has_deferred_in_limit:
                    imap.store(num, "+FLAGS", "\\Seen")
                if created:
                    processed += 1
                    drafts.extend(created)
                else:
                    skipped += 1

        return EmailPollResult(
            connected=True,
            inbox_address=status.inbox_address,
            processed_count=processed,
            skipped_count=skipped,
            drafts=drafts,
        )

    # ─────────────────────────── Door 3: Watched Folder ───────────────────────────

    def set_programme_watched_folder(self, programme_id: str, watched_folder: str | None) -> ContractFull:
        prog = self.contract_store.get_contract(programme_id)
        if not prog:
            raise KeyError(f"Programme not found: {programme_id}")
        cleaned: str | None = None
        if watched_folder and watched_folder.strip():
            cleaned, resolved = resolve_safe_watched_folder(watched_folder)
            resolved.mkdir(parents=True, exist_ok=True)
        updated = self.contract_store.update_contract(programme_id, ContractUpdate(watched_folder=cleaned))
        if not updated:
            raise KeyError(f"Programme not found: {programme_id}")
        if self.on_cms_mutated:
            self.on_cms_mutated()
        return updated

    def get_folder_status(self) -> FolderDoorStatus:
        root = get_submissions_folder_root()
        contracts = self.contract_store.list_contracts()
        with get_session() as db:
            ingested_keys = {
                r.external_key
                for r in db.query(WatchedIngestionRecord.external_key)
                .filter(WatchedIngestionRecord.door == IntakeDoor.FOLDER.value)
                .all()
            }

        watched: list[WatchedProgrammeFolder] = []
        for c in contracts:
            if not c.watched_folder:
                continue
            try:
                cleaned, resolved = resolve_safe_watched_folder(c.watched_folder)
            except ValueError:
                continue
            exists = resolved.is_dir()
            pending = 0
            if exists:
                for p in sorted(resolved.iterdir()):
                    if not p.is_file() or p.name.startswith(".") or p.suffix.lower() not in SUPPORTED_INTAKE_EXTS:
                        continue
                    st = p.stat()
                    key = f"folder:{c.programme_id}:{p.name}:{st.st_mtime_ns}:{st.st_size}"
                    if key not in ingested_keys:
                        pending += 1
            watched.append(
                WatchedProgrammeFolder(
                    programme_id=c.programme_id,
                    cedent_name=c.cedent_name,
                    programme_name=c.programme_name,
                    watched_folder=cleaned,
                    resolved_exists=exists,
                    pending_files=pending,
                )
            )
        return FolderDoorStatus(root_dir=str(root), exists=root.is_dir(), watched_programmes=watched)

    def scan_watched_folders(self, programme_id: str | None = None) -> FolderScanResult:
        """Scan configured programme watched folders and create drafts for new files."""
        root = get_submissions_folder_root()
        contracts = self.contract_store.list_contracts()
        if programme_id:
            contracts = [c for c in contracts if c.programme_id == programme_id]

        with get_session() as db:
            ingested_keys = {
                r.external_key
                for r in db.query(WatchedIngestionRecord.external_key)
                .filter(WatchedIngestionRecord.door == IntakeDoor.FOLDER.value)
                .all()
            }

        scanned = 0
        picked_up = 0
        skipped = 0
        drafts: list[SubmissionDraftOut] = []

        for c in contracts:
            if not c.watched_folder:
                continue
            try:
                cleaned, resolved = resolve_safe_watched_folder(c.watched_folder)
            except ValueError:
                continue
            if not resolved.is_dir():
                continue
            scanned += 1
            for p in sorted(resolved.iterdir()):
                if not p.is_file() or p.name.startswith(".") or p.suffix.lower() not in SUPPORTED_INTAKE_EXTS:
                    continue
                st = p.stat()
                if st.st_size > MAX_UPLOAD_BYTES:
                    skipped += 1
                    continue
                ext_key = f"folder:{c.programme_id}:{p.name}:{st.st_mtime_ns}:{st.st_size}"
                if ext_key in ingested_keys:
                    skipped += 1
                    continue
                raw = p.read_bytes()
                draft = self.ingest_file(
                    raw_bytes=raw,
                    filename=p.name,
                    door=IntakeDoor.FOLDER,
                    source_ref=f"{cleaned}/{p.name}",
                    target_programme_id=c.programme_id,
                    dedup_key=ext_key,
                )
                ingested_keys.add(ext_key)
                picked_up += 1
                drafts.append(draft)

        return FolderScanResult(
            root_dir=str(root),
            scanned_programmes=scanned,
            picked_up_count=picked_up,
            skipped_count=skipped,
            drafts=drafts,
        )

    # ─────────────────────────── Review, Confirm, Discard ───────────────────────────

    def get_status(self) -> IntakeStatusResponse:
        with get_session() as db:
            pending_count = (
                db.query(SubmissionDraftRecord)
                .filter(SubmissionDraftRecord.status == IntakeDraftStatus.PENDING_REVIEW.value)
                .count()
            )
        return IntakeStatusResponse(
            email=self.get_email_status(),
            folder=self.get_folder_status(),
            pending_count=pending_count,
            max_upload_mb=MAX_UPLOAD_MB,
        )

    def list_drafts(
        self,
        *,
        status: IntakeDraftStatus | None = None,
        programme_id: str | None = None,
        door: IntakeDoor | None = None,
    ) -> list[SubmissionDraftOut]:
        with get_session() as db:
            q = db.query(SubmissionDraftRecord)
            if status is not None:
                q = q.filter(SubmissionDraftRecord.status == status.value)
            if programme_id is not None:
                q = q.filter(SubmissionDraftRecord.target_programme_id == programme_id)
            if door is not None:
                q = q.filter(SubmissionDraftRecord.door == door.value)
            recs = q.order_by(SubmissionDraftRecord.created_at.desc()).all()

        contracts_by_id = {c.programme_id: c for c in self.contract_store.list_contracts()}
        sim_sets_by_id = {s.sim_set_id: (s.name, list(s.loss_files)) for s in self.sim_store.list_sim_sets()}
        return [
            self._record_to_out(r, contracts_by_id=contracts_by_id, sim_sets_by_id=sim_sets_by_id)
            for r in recs
        ]

    def get_draft(self, draft_id: str) -> SubmissionDraftOut | None:
        with get_session() as db:
            rec = db.get(SubmissionDraftRecord, draft_id)
            return self._record_to_out(rec) if rec else None

    def update_draft(self, draft_id: str, patch: SubmissionDraftUpdate) -> SubmissionDraftOut | None:
        with get_session() as db:
            rec = db.get(SubmissionDraftRecord, draft_id)
            if not rec:
                return None
            if rec.status != IntakeDraftStatus.PENDING_REVIEW.value:
                raise ValueError(f"Draft '{draft_id}' is already {rec.status} and cannot be modified.")

            extracted = IntakeExtractedData(**json.loads(rec.extracted_json or "{}"))
            re_sketch = False

            if "target_programme_id" in patch.model_fields_set:
                rec.target_programme_id = patch.target_programme_id
                re_sketch = True
            if patch.sim_set_id is not None:
                rec.sim_set_id = patch.sim_set_id
                re_sketch = True
            if patch.extracted is not None:
                extracted = patch.extracted
                rec.extracted_json = extracted.model_dump_json()
                re_sketch = True

            if patch.graph is not None:
                rec.graph_json = patch.graph.model_dump_json()
            elif re_sketch:
                sim_id = rec.sim_set_id or self.base_sim_id
                _, loss_files = self._sim_set_metadata(sim_id)
                graph = self.sketch_draft_graph(
                    draft_id=rec.draft_id,
                    extracted=extracted,
                    sim_set_id=sim_id,
                    loss_files=loss_files,
                    target_programme_id=rec.target_programme_id,
                )
                rec.graph_json = graph.model_dump_json()

            rec.updated_at = _utcnow()
            db.commit()
            db.refresh(rec)
            return self._record_to_out(rec)

    def discard_draft(self, draft_id: str) -> SubmissionDraftOut | None:
        """Discard a draft without writing anything to CMS."""
        with get_session() as db:
            rec = db.get(SubmissionDraftRecord, draft_id)
            if not rec:
                return None
            if rec.status == IntakeDraftStatus.CONFIRMED.value:
                raise ValueError(f"Draft '{draft_id}' is already CONFIRMED and cannot be discarded.")
            rec.status = IntakeDraftStatus.DISCARDED.value
            rec.decided_at = _utcnow()
            rec.updated_at = _utcnow()
            db.commit()
            db.refresh(rec)
            return self._record_to_out(rec)

    def confirm_draft(self, draft_id: str, req: SubmissionConfirmRequest | None = None) -> SubmissionConfirmResponse:
        """Confirm a draft and write it into CMS as a SUBMISSION draft (never BOUND)."""
        req = req or SubmissionConfirmRequest()
        with get_session() as db:
            rec = db.get(SubmissionDraftRecord, draft_id)
            if not rec:
                raise KeyError(f"Submission draft not found: {draft_id}")
            if rec.status != IntakeDraftStatus.PENDING_REVIEW.value:
                raise ValueError(f"Draft '{draft_id}' is already {rec.status}.")

            extracted = req.extracted or IntakeExtractedData(**json.loads(rec.extracted_json or "{}"))
            target_id = req.target_programme_id if "target_programme_id" in req.model_fields_set else rec.target_programme_id
            sim_id = rec.sim_set_id or self.base_sim_id

            existing_prog = self.contract_store.get_contract(target_id) if target_id else None
            attach_existing = (req.mode == "ATTACH_EXISTING" and existing_prog is not None) or (
                req.mode is None and existing_prog is not None
            )

            if attach_existing and existing_prog is not None:
                # Attach loss source and/or layers to existing programme WITHOUT changing its status
                contract = existing_prog
                if sim_id and sim_id != contract.sim_set_id and rec.source_file_type in (IntakeFileKind.YELT.value, IntakeFileKind.ELT.value):
                    updated = self.contract_store.update_contract(contract.programme_id, ContractUpdate(sim_set_id=sim_id))
                    if updated:
                        contract = updated

                if extracted.layers:
                    layer_specs = [
                        _layer_spec_from_draft(
                            lyr,
                            layer_code=None,
                            fallback_name=lyr.layer_code or f"Layer {i + 1}",
                        )
                        for i, lyr in enumerate(extracted.layers)
                    ]
                    updated = self.contract_store.add_layers(contract.programme_id, layer_specs)
                    if updated is None:
                        raise KeyError(f"Programme not found: {contract.programme_id}")
                    contract = updated

                action: str = "UPDATED_PROGRAMME"
            else:
                # Create a brand new SUBMISSION draft programme in CMS (never BOUND)
                _, loss_files = self._sim_set_metadata(sim_id)
                available_rps = {lf.region_peril for lf in loss_files}
                reviewed_rps = [rp for rp in extracted.region_perils if rp and rp != "ALL"]
                missing_rps = [rp for rp in reviewed_rps if available_rps and rp not in available_rps]
                if missing_rps:
                    avail_str = ", ".join(sorted(available_rps))
                    raise DraftValidationError(
                        f"Cannot confirm draft: region/peril key(s) {', '.join(missing_rps)} are not present in "
                        f"attached loss source '{sim_id}'. Available: {avail_str}. "
                        f"Update the reviewed region/perils or attach a matching loss file."
                    )
                final_rps = reviewed_rps or (["US-FL-WS"] if (not available_rps or "US-FL-WS" in available_rps) else [sorted(available_rps)[0]])

                layer_specs = [
                    _layer_spec_from_draft(
                        lyr,
                        layer_code=lyr.layer_code or f"L{i + 1}",
                        fallback_name=f"Layer {i + 1}",
                    )
                    for i, lyr in enumerate(extracted.layers)
                ]
                total_limit = sum(l.limit for l in extracted.layers) if extracted.layers else 25.0
                min_att = min(l.attachment for l in extracted.layers) if extracted.layers else 50.0
                total_prem = (
                    sum(
                        l.deposit_premium if l.deposit_premium > 0 else round(l.limit * (l.rol or 0.0), 4)
                        for l in extracted.layers
                    )
                    if extracted.layers
                    else 4.0
                )
                eff_rol = round(total_prem / total_limit, 6) if total_limit > 0 else 0.16
                signed_line = extracted.layers[0].signed_line if extracted.layers else 1.0
                written_line = extracted.layers[0].written_line if extracted.layers else 1.0

                payload = ContractCreate(
                    cedent_name=extracted.cedent_name or "Draft Cedent",
                    programme_name=extracted.programme_name or f"Submission Draft ({rec.source_filename})",
                    treaty_year=extracted.treaty_year or 2027,
                    renewal_period=extracted.renewal_period or "J27",
                    broker=extracted.broker or "Direct",
                    status=ProgrammeStatus.SUBMISSION,
                    inception_date=extracted.inception_date or "2027-01-01",
                    expiry_date=extracted.expiry_date or "2027-12-31",
                    written_line=written_line,
                    signed_line=signed_line,
                    limit=total_limit,
                    attachment=min_att,
                    deposit_premium=total_prem,
                    rol=eff_rol,
                    currency=extracted.currency or "USD",
                    sim_set_id=sim_id,
                    region_perils=final_rps,
                    perils=_valid_peril_codes(extracted.perils, final_rps),
                    layers=layer_specs,
                    custom_tags=["INTAKE_DRAFT", f"DOOR_{rec.door}"],
                )
                contract = self.contract_store.create_contract(payload, self.lineage)
                action = "CREATED_DRAFT_PROGRAMME"

            # Record intake provenance & loss history as an underwriting note on the programme
            note_lines = [
                f"Confirmed from submission intake ({rec.door}) — source: {rec.source_filename} ({rec.source_file_type}).",
            ]
            if extracted.loss_history:
                note_lines.append("Extracted Loss History:")
                for lh in extracted.loss_history:
                    yr_str = f"{lh.year} " if lh.year else ""
                    note_lines.append(f"- {yr_str}{lh.event_name}: {lh.currency} {lh.ground_up_loss_m:g}M")
            db.add(
                ContractNoteRecord(
                    note_id=f"note-{uuid.uuid4().hex[:8]}",
                    programme_id=contract.programme_id,
                    cedent_name=contract.cedent_name,
                    author="Submission Intake",
                    category="UNDERWRITING",
                    body="\n".join(note_lines),
                )
            )

            rec.status = IntakeDraftStatus.CONFIRMED.value
            rec.confirmed_programme_id = contract.programme_id
            rec.extracted_json = extracted.model_dump_json()
            rec.graph_json = contract.graph.model_dump_json()
            rec.decided_at = _utcnow()
            rec.updated_at = _utcnow()
            db.commit()
            db.refresh(rec)

            manifest = self.lineage.record_manifest(
                run_type="SUBMISSION_INTAKE_CONFIRM",
                input_hashes={"source": rec.source_hash, "draft": rec.draft_id},
                parameters={
                    "door": rec.door,
                    "source_file_type": rec.source_file_type,
                    "action": action,
                    "programme_id": contract.programme_id,
                },
                output_payload={
                    "programme_id": contract.programme_id,
                    "status": contract.status.value,
                    "sim_set_id": contract.sim_set_id,
                },
                breadcrumb=[
                    BreadcrumbStep(entity_type="SUBMISSION_DRAFT", entity_id=rec.draft_id, label=rec.source_filename, content_hash=rec.source_hash),
                    BreadcrumbStep(entity_type="CONTRACT", entity_id=contract.programme_id, label=contract.programme_name, content_hash=contract.content_hash),
                ],
            )

            if self.on_cms_mutated:
                self.on_cms_mutated()

            return SubmissionConfirmResponse(
                draft=self._record_to_out(rec),
                contract=contract,
                action=action,  # type: ignore[arg-type]
                manifest=manifest,
            )
