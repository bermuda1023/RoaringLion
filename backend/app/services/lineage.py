"""Universal Linkage Graph & Breadcrumb Audit Manifest Ledger."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models.domain import BreadcrumbStep, LineageLink, RunManifest
from app.models.enums import LinkType


def canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class LineageRegistry:
    """In-memory + append-only manifest & link store for Phase 0."""

    def __init__(self) -> None:
        self.manifests: dict[str, RunManifest] = {}
        self.links: list[LineageLink] = []

    def add_link(self, source_id: str, target_id: str, link_type: LinkType) -> LineageLink:
        link = LineageLink(
            source_id=source_id,
            target_id=target_id,
            link_type=link_type,
            created_at=datetime.now(timezone.utc),
        )
        self.links.append(link)
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
        manifest = RunManifest(
            manifest_id=manifest_id,
            run_type=run_type,
            created_at=datetime.now(timezone.utc),
            actor=actor,
            input_hashes=input_hashes,
            parameters=parameters,
            output_hash=out_hash,
            breadcrumb=breadcrumb,
        )
        self.manifests[manifest_id] = manifest
        for step in breadcrumb:
            self.add_link(manifest_id, step.entity_id, LinkType.DERIVED_FROM)
        return manifest

    def get_manifest(self, manifest_id: str) -> RunManifest | None:
        return self.manifests.get(manifest_id)

    def list_manifests(self) -> list[RunManifest]:
        return sorted(self.manifests.values(), key=lambda m: m.created_at, reverse=True)

    def get_links_for(self, entity_id: str) -> list[LineageLink]:
        return [l for l in self.links if l.source_id == entity_id or l.target_id == entity_id]
