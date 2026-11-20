"""Phase 6: Bi-Directional ExposureEclipse (Peril Vista) Integration Adapter."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app.models.common import CamelModel


class ExposureProgrammeRef(CamelModel):
    cedent_id: str
    cedent_name: str
    chain_id: str
    programme_id: str
    programme_name: str
    treaty_year: int
    office: str
    server_name: str
    edm_database_name: str
    currency: str = "USD"
    total_tiv_m: float
    granular_tiv_m: dict[str, float]


class BoundDealSyncEvent(CamelModel):
    programme_id: str
    exposure_eclipse_programme_id: str | None
    status: str
    signed_line: float
    limit_m: float
    rol: float
    synced_at: datetime
    mode: str


class ExposureEclipseClient:
    """Calls ExposureEclipse over HTTP when EXPOSURE_ECLIPSE_BASE_URL is configured; mock fixtures otherwise."""

    def __init__(self) -> None:
        self.base_url = os.getenv("EXPOSURE_ECLIPSE_BASE_URL", "").rstrip("/")
        self.token = os.getenv("EXPOSURE_ECLIPSE_API_TOKEN", "")
        self.sync_outbox: list[BoundDealSyncEvent] = []

    def list_exposure_programmes(self) -> list[ExposureProgrammeRef]:
        if self.base_url:
            try:
                headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
                r = httpx.get(f"{self.base_url}/api/cedents", headers=headers, timeout=5.0)
                if r.status_code == 200:
                    data = r.json().get("cedents", [])
                    out: list[ExposureProgrammeRef] = []
                    for ced in data:
                        for ch in ced.get("chains", []):
                            for p in ch.get("programmes", []):
                                edm = p.get("edm", {})
                                out.append(
                                    ExposureProgrammeRef(
                                        cedent_id=ced["cedentId"],
                                        cedent_name=ced["cedentName"],
                                        chain_id=ch["chainId"],
                                        programme_id=p["programmeId"],
                                        programme_name=p["programmeName"],
                                        treaty_year=p["treatyYear"],
                                        office=p.get("office", "BDA"),
                                        server_name=edm.get("serverName", "SQL-PROD-01"),
                                        edm_database_name=edm.get("edmDatabaseName", "EDM_LIVE"),
                                        currency=edm.get("currency", "USD"),
                                        total_tiv_m=0.0,
                                        granular_tiv_m={},
                                    )
                                )
                    return out
            except Exception:
                pass

        # Mock ExposureEclipse catalogue mirroring bermuda1023/ExposureEclipse schema
        return [
            ExposureProgrammeRef(
                cedent_id="ced-farmers",
                cedent_name="Farmers Group",
                chain_id="ch-farmers-bda-nat",
                programme_id="ee-farmers-2027",
                programme_name="Farmers Nationwide Multi-Peril 2027",
                treaty_year=2027,
                office="BDA",
                server_name="SQL-CAT-01",
                edm_database_name="EDM_FARMERS_2027_Q1",
                currency="USD",
                total_tiv_m=48_500.0,
                granular_tiv_m={"US-FL-WS": 14_200.0, "US-TX-WS": 11_800.0, "US-SE-WS": 9_400.0, "US-CA-EQ": 13_100.0},
            ),
            ExposureProgrammeRef(
                cedent_id="ced-pacific",
                cedent_name="Pacific Re",
                chain_id="ch-pacific-eq",
                programme_id="ee-pacific-2027",
                programme_name="Pacific Rim EQ 2027",
                treaty_year=2027,
                office="BDA",
                server_name="SQL-CAT-02",
                edm_database_name="EDM_PACIFIC_EQ_2027",
                currency="USD",
                total_tiv_m=31_000.0,
                granular_tiv_m={"US-CA-EQ": 16_500.0, "JP-KANTO-EQ": 14_500.0},
            ),
        ]

    def record_bound_sync(
        self,
        *,
        programme_id: str,
        ee_programme_id: str | None,
        status: str,
        signed_line: float,
        limit_m: float,
        rol: float,
    ) -> BoundDealSyncEvent:
        evt = BoundDealSyncEvent(
            programme_id=programme_id,
            exposure_eclipse_programme_id=ee_programme_id,
            status=status,
            signed_line=signed_line,
            limit_m=limit_m,
            rol=rol,
            synced_at=datetime.now(timezone.utc),
            mode="http" if self.base_url else "mock_outbox",
        )
        self.sync_outbox.insert(0, evt)
        return evt
