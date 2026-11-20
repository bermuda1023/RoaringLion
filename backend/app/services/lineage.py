"""Universal Linkage Graph & Breadcrumb Audit Manifest Ledger (SQLAlchemy-persisted + staleness detection)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.database import get_session
from app.db.models import LineageLinkRecord, LineageManifestRecord
from app.models.domain import BreadcrumbStep, LineageLink, RunManifest
from app.models.enums import LinkType
from app.models.phase1 import StaleManifestInfo


def canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class LineageRegistry:
    """Persistent append-only breadcrumb manifest & bidirectional linkage graph."""

    def __init__(self, persist: bool = True) -> None:
        self.persist = persist
        self._mem_manifests: dict[str, RunManifest] = {}
        self._mem_links: list[LineageLink] = []

    def add_link(self, source_id: str, target_id: str, link_type: LinkType) -> LineageLink:
        now = datetime.now(timezone.utc)
        link = LineageLink(
            source_id=source_id,
            target_id=target_id,
            link_type=link_type,
            created_at=now,
        )
        self._mem_links.append(link)
        if self.persist:
            with get_session() as db:
                db.add(
                    LineageLinkRecord(
                        source_id=source_id,
                        target_id=target_id,
                        link_type=link_type.value,
                        created_at=now,
                    )
                )
                db.commit()
        return link

    def record_manifest(
        self,
        *,
        run_type: str,
        input_hashes: dict[str, str],
        parameters: dict[str, Any],
        output_payload: Any,
        breadcrumb: list[BreadcrumbStep],
        actor: str = "underwriter",
    ) -> RunManifest:
        manifest_id = f"man-{run_type.lower()}-{uuid.uuid4().hex[:10]}"
        out_hash = canonical_hash(output_payload)
        now = datetime.now(timezone.utc)
        manifest = RunManifest(
            manifest_id=manifest_id,
            run_type=run_type,
            created_at=now,
            actor=actor,
            input_hashes=input_hashes,
            parameters=parameters,
            output_hash=out_hash,
            breadcrumb=breadcrumb,
        )
        self._mem_manifests[manifest_id] = manifest
        if self.persist:
            with get_session() as db:
                db.add(
                    LineageManifestRecord(
                        manifest_id=manifest_id,
                        run_type=run_type,
                        created_at=now,
                        actor=actor,
                        engine_version=manifest.engine_version,
                        input_hashes_json=json.dumps(input_hashes),
                        parameters_json=json.dumps(parameters, default=str),
                        output_hash=out_hash,
                        breadcrumb_json=json.dumps([b.model_dump() for b in breadcrumb]),
                    )
                )
                for step in breadcrumb:
                    db.add(
                        LineageLinkRecord(
                            source_id=manifest_id,
                            target_id=step.entity_id,
                            link_type=LinkType.DERIVED_FROM.value,
                            created_at=now,
                        )
                    )
                db.commit()
        else:
            for step in breadcrumb:
                self._mem_links.append(
                    LineageLink(
                        source_id=manifest_id,
                        target_id=step.entity_id,
                        link_type=LinkType.DERIVED_FROM,
                        created_at=now,
                    )
                )
        return manifest

    def _to_manifest(self, rec: LineageManifestRecord) -> RunManifest:
        return RunManifest(
            manifest_id=rec.manifest_id,
            run_type=rec.run_type,
            created_at=rec.created_at,
            actor=rec.actor,
            engine_version=rec.engine_version,
            input_hashes=json.loads(rec.input_hashes_json),
            parameters=json.loads(rec.parameters_json),
            output_hash=rec.output_hash,
            breadcrumb=[BreadcrumbStep(**b) for b in json.loads(rec.breadcrumb_json)],
        )

    def get_manifest(self, manifest_id: str) -> RunManifest | None:
        if manifest_id in self._mem_manifests:
            return self._mem_manifests[manifest_id]
        if self.persist:
            with get_session() as db:
                rec = db.get(LineageManifestRecord, manifest_id)
                if rec:
                    return self._to_manifest(rec)
        return None

    def list_manifests(self, limit: int = 100) -> list[RunManifest]:
        if self.persist:
            with get_session() as db:
                recs = (
                    db.query(LineageManifestRecord)
                    .order_by(LineageManifestRecord.created_at.desc())
                    .limit(limit)
                    .all()
                )
                return [self._to_manifest(r) for r in recs]
        return sorted(self._mem_manifests.values(), key=lambda m: m.created_at, reverse=True)[:limit]

    def get_links_for(self, entity_id: str) -> list[LineageLink]:
        if self.persist:
            with get_session() as db:
                recs = (
                    db.query(LineageLinkRecord)
                    .filter((LineageLinkRecord.source_id == entity_id) | (LineageLinkRecord.target_id == entity_id))
                    .all()
                )
                return [
                    LineageLink(
                        source_id=r.source_id,
                        target_id=r.target_id,
                        link_type=LinkType(r.link_type),
                        created_at=r.created_at,
                    )
                    for r in recs
                ]
        return [l for l in self._mem_links if l.source_id == entity_id or l.target_id == entity_id]

    def check_staleness(self, current_entity_hashes: dict[str, str]) -> list[StaleManifestInfo]:
        """Walk recorded manifests and flag any whose breadcrumb/input hash no longer matches current state."""
        results: list[StaleManifestInfo] = []
        for m in self.list_manifests(limit=200):
            reasons: list[str] = []
            for step in m.breadcrumb:
                cur_h = current_entity_hashes.get(step.entity_id)
                if cur_h and step.content_hash and cur_h != step.content_hash:
                    reasons.append(f"{step.entity_type}:{step.entity_id} changed ({step.content_hash[:8]} -> {cur_h[:8]})")
            results.append(
                StaleManifestInfo(
                    manifest_id=m.manifest_id,
                    run_type=m.run_type,
                    is_stale=len(reasons) > 0,
                    stale_reasons=reasons,
                )
            )
        return results
