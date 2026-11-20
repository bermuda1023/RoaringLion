"""Persistent Contract Management System (CMS) Ledger & One-Click Sliced Rollup Service."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.db.database import get_session
from app.db.models import ContractRecord
from app.models.domain import (
    GraphEdgeSpec,
    GraphNodeSpec,
    GraphSpec,
    ProgrammeDeal,
    UpliftRule,
    UpliftSet,
)
from app.models.enums import (
    ContractDirection,
    InstrumentType,
    LinkType,
    NodeType,
    PerilCode,
    PlacementType,
    ProgrammeStatus,
    StructureType,
)
from app.models.phase1 import ContractCreate, ContractFull, ContractUpdate, IlwPriceRequest, SliceRollupRequest
from app.services.lineage import LineageRegistry, canonical_hash
from app.services.seed_data import _build_eurowind_programme, _build_farmers_programme, _build_pacific_eq_programme


def _default_graph_for_contract(c: ContractCreate, prog_id: str) -> GraphSpec:
    nodes = [
        GraphNodeSpec(
            node_id=f"src-{prog_id}",
            node_type=NodeType.LOSS_SOURCE,
            label=f"Loss Source ({c.sim_set_id})",
            params={"simSetId": c.sim_set_id},
            position={"x": 40, "y": 100},
        ),
        GraphNodeSpec(
            node_id=f"flt-{prog_id}",
            node_type=NodeType.REGION_PERIL_FILTER,
            label=f"Zone Peril ({', '.join(c.region_perils)})",
            params={"regionPerils": c.region_perils},
            position={"x": 300, "y": 100},
        ),
        GraphNodeSpec(
            node_id=f"lyr-{prog_id}",
            node_type=NodeType.AGG_XL if c.structure == StructureType.AGG_XL else NodeType.OCC_XL,
            label=f"{c.programme_name} — {c.limit:g}m xs {c.attachment:g}m",
            params=(
                {
                    "aggLimit": c.limit,
                    "aggAttachment": c.attachment,
                    "perEventFranchise": 0.0,
                    "depositPremium": c.deposit_premium,
                    "placedShare": c.signed_line,
                }
                if c.structure == StructureType.AGG_XL
                else {
                    "occLimit": c.limit,
                    "occAttachment": c.attachment,
                    "occFranchise": 0.0,
                    "reinstatements": 1.0,
                    "ripRate": 1.0,
                    "depositPremium": c.deposit_premium,
                    "placedShare": c.signed_line,
                }
            ),
            position={"x": 600, "y": 100},
        ),
        GraphNodeSpec(
            node_id=f"out-{prog_id}",
            node_type=NodeType.YELT_OUTPUT,
            label="YELT Output",
            params={},
            position={"x": 920, "y": 100},
        ),
    ]
    edges = [
        GraphEdgeSpec(edge_id=f"e1-{prog_id}", source=f"src-{prog_id}", target=f"flt-{prog_id}", sign=1, weight=1.0),
        GraphEdgeSpec(edge_id=f"e2-{prog_id}", source=f"flt-{prog_id}", target=f"lyr-{prog_id}", sign=1, weight=1.0),
        GraphEdgeSpec(edge_id=f"e3-{prog_id}", source=f"lyr-{prog_id}", target=f"out-{prog_id}", sign=1, weight=1.0),
    ]
    return GraphSpec(
        graph_id=f"grp-{prog_id}",
        name=f"{c.cedent_name} — {c.programme_name}",
        version=1,
        currency=c.currency,
        nodes=nodes,
        edges=edges,
        content_hash=canonical_hash({"nodes": [n.model_dump() for n in nodes], "edges": [e.model_dump() for e in edges]}),
    )


def _record_to_full(r: ContractRecord) -> ContractFull:
    return ContractFull(
        programme_id=r.programme_id,
        chain_id=r.chain_id,
        prior_contract_id=r.prior_contract_id,
        cedent_name=r.cedent_name,
        programme_name=r.programme_name,
        treaty_year=r.treaty_year,
        renewal_period=r.renewal_period,
        office=r.office,
        underwriter=r.underwriter,
        secondary_underwriter=r.secondary_underwriter,
        broker=r.broker,
        fronting_carrier=r.fronting_carrier,
        collateral_cell=r.collateral_cell,
        status=ProgrammeStatus(r.status),
        direction=ContractDirection(r.direction),
        structure=StructureType(r.structure),
        instrument=InstrumentType(r.instrument),
        placement=PlacementType(r.placement),
        inception_date=r.inception_date,
        expiry_date=r.expiry_date,
        hours_clause=r.hours_clause,
        authorized_line=r.authorized_line,
        written_line=r.written_line,
        signed_line=r.signed_line,
        limit=r.limit,
        attachment=r.attachment,
        deposit_premium=r.deposit_premium,
        rol=r.rol,
        technical_rol=r.technical_rol,
        brokerage_pct=r.brokerage_pct,
        fronting_fee_pct=r.fronting_fee_pct,
        collateral_required=r.collateral_required,
        currency=r.currency,
        sim_set_id=r.sim_set_id,
        exposure_eclipse_programme_id=r.exposure_eclipse_programme_id,
        region_perils=json.loads(r.region_perils_json),
        perils=[PerilCode(p) for p in json.loads(r.perils_json)],
        custom_tags=json.loads(r.custom_tags_json),
        uplift_set=UpliftSet(**json.loads(r.uplift_set_json)),
        graph=GraphSpec(**json.loads(r.graph_json)),
        ilw_spec=json.loads(r.ilw_spec_json) if r.ilw_spec_json else None,
        version=r.version,
        content_hash=r.content_hash,
        updated_at=r.updated_at,
    )


def contract_to_programme_deal(c: ContractFull) -> ProgrammeDeal:
    return ProgrammeDeal(
        programme_id=c.programme_id,
        cedent_name=c.cedent_name,
        programme_name=c.programme_name,
        treaty_year=c.treaty_year,
        renewal_period=c.renewal_period,
        office=c.office,
        underwriter=c.underwriter,
        broker=c.broker,
        status=c.status,
        currency=c.currency,
        sim_set_id=c.sim_set_id,
        uplift_set=c.uplift_set,
        graph=c.graph,
        written_line=c.written_line,
        signed_line=c.signed_line,
        deposit_premium=c.deposit_premium,
        limit=c.limit,
        rol=c.rol,
        collateral_required=c.collateral_required,
        region_perils=c.region_perils,
        perils=c.perils,
    )


class ContractStore:
    """SQLAlchemy-backed CMS Contract Ledger."""

    def ensure_seeded(self, default_sim_set_id: str) -> None:
        with get_session() as db:
            if db.query(ContractRecord).count() > 0:
                return
            for prog in (
                _build_farmers_programme(default_sim_set_id),
                _build_pacific_eq_programme(default_sim_set_id),
                _build_eurowind_programme(default_sim_set_id),
            ):
                chash = canonical_hash(prog.model_dump())
                rec = ContractRecord(
                    programme_id=prog.programme_id,
                    chain_id=f"chain-{prog.programme_id}",
                    cedent_name=prog.cedent_name,
                    programme_name=prog.programme_name,
                    treaty_year=prog.treaty_year,
                    renewal_period=prog.renewal_period,
                    office=prog.office,
                    underwriter=prog.underwriter,
                    broker=prog.broker,
                    fronting_carrier="Keystone Re Transformer",
                    collateral_cell=f"Cell-{prog.renewal_period}",
                    status=prog.status.value,
                    direction=ContractDirection.ASSUMED.value,
                    structure=StructureType.OCC_XL.value,
                    instrument=InstrumentType.UNL.value,
                    placement=PlacementType.OPEN_MARKET.value,
                    inception_date=f"{prog.treaty_year}-01-01",
                    expiry_date=f"{prog.treaty_year}-12-31",
                    hours_clause=72,
                    authorized_line=prog.written_line,
                    written_line=prog.written_line,
                    signed_line=prog.signed_line,
                    limit=prog.limit,
                    attachment=80.0 if "farmers" in prog.programme_id else (70.0 if "pacific" in prog.programme_id else 45.0),
                    deposit_premium=prog.deposit_premium,
                    rol=prog.rol,
                    technical_rol=round(prog.rol * 0.92, 4),
                    brokerage_pct=0.10,
                    fronting_fee_pct=0.02,
                    collateral_required=prog.collateral_required,
                    currency=prog.currency,
                    sim_set_id=prog.sim_set_id,
                    region_perils_json=json.dumps(prog.region_perils),
                    perils_json=json.dumps([p.value for p in prog.perils]),
                    custom_tags_json=json.dumps(["Peak" if "US-FL-WS" in prog.region_perils else "Diversifier"]),
                    uplift_set_json=prog.uplift_set.model_dump_json(),
                    graph_json=prog.graph.model_dump_json(),
                    version=1,
                    content_hash=chash,
                )
                db.add(rec)
            db.commit()

    def list_contracts(
        self,
        *,
        status: ProgrammeStatus | None = None,
        direction: ContractDirection | None = None,
        underwriter: str | None = None,
        office: str | None = None,
        renewal_period: str | None = None,
        search: str | None = None,
    ) -> list[ContractFull]:
        with get_session() as db:
            q = db.query(ContractRecord)
            if status:
                q = q.filter(ContractRecord.status == status.value)
            if direction:
                q = q.filter(ContractRecord.direction == direction.value)
            if underwriter:
                q = q.filter(ContractRecord.underwriter == underwriter)
            if office:
                q = q.filter(ContractRecord.office == office)
            if renewal_period:
                q = q.filter(ContractRecord.renewal_period == renewal_period)
            recs = q.order_by(ContractRecord.updated_at.desc()).all()
            out = [_record_to_full(r) for r in recs]
            if search:
                s = search.lower()
                out = [
                    c for c in out
                    if s in c.cedent_name.lower()
                    or s in c.programme_name.lower()
                    or s in c.broker.lower()
                    or s in c.programme_id.lower()
                ]
            return out

    def get_contract(self, programme_id: str) -> ContractFull | None:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            return _record_to_full(rec) if rec else None

    def create_contract(self, payload: ContractCreate, lineage: LineageRegistry) -> ContractFull:
        prog_id = payload.programme_id or f"prog-{uuid.uuid4().hex[:8]}"
        uplift = payload.uplift_set or UpliftSet(
            uplift_set_id=f"upl-{prog_id}",
            name=f"{payload.programme_name} Uplift",
            version=1,
            rules=[],
            content_hash=canonical_hash([]),
        )
        graph = payload.graph or _default_graph_for_contract(payload, prog_id)
        chash = canonical_hash({
            "programme_id": prog_id,
            "limit": payload.limit,
            "signed_line": payload.signed_line,
            "rol": payload.rol,
            "graph": graph.content_hash,
            "uplift": uplift.content_hash,
        })
        rec = ContractRecord(
            programme_id=prog_id,
            chain_id=payload.chain_id or f"chain-{prog_id}",
            prior_contract_id=payload.prior_contract_id,
            cedent_name=payload.cedent_name,
            programme_name=payload.programme_name,
            treaty_year=payload.treaty_year,
            renewal_period=payload.renewal_period,
            office=payload.office,
            underwriter=payload.underwriter,
            secondary_underwriter=payload.secondary_underwriter,
            broker=payload.broker,
            fronting_carrier=payload.fronting_carrier,
            collateral_cell=payload.collateral_cell,
            status=payload.status.value,
            direction=payload.direction.value,
            structure=payload.structure.value,
            instrument=payload.instrument.value,
            placement=payload.placement.value,
            inception_date=payload.inception_date,
            expiry_date=payload.expiry_date,
            hours_clause=payload.hours_clause,
            authorized_line=payload.authorized_line,
            written_line=payload.written_line,
            signed_line=payload.signed_line,
            limit=payload.limit,
            attachment=payload.attachment,
            deposit_premium=payload.deposit_premium,
            rol=payload.rol,
            technical_rol=payload.technical_rol,
            brokerage_pct=payload.brokerage_pct,
            fronting_fee_pct=payload.fronting_fee_pct,
            collateral_required=payload.collateral_required,
            currency=payload.currency,
            sim_set_id=payload.sim_set_id,
            exposure_eclipse_programme_id=payload.exposure_eclipse_programme_id,
            region_perils_json=json.dumps(payload.region_perils),
            perils_json=json.dumps([p.value for p in payload.perils]),
            custom_tags_json=json.dumps(payload.custom_tags),
            uplift_set_json=uplift.model_dump_json(),
            graph_json=graph.model_dump_json(),
            ilw_spec_json=json.dumps(payload.ilw_spec) if payload.ilw_spec else None,
            version=1,
            content_hash=chash,
        )
        with get_session() as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
            full = _record_to_full(rec)

        lineage.add_link(prog_id, payload.sim_set_id, LinkType.USES_LOSSES)
        lineage.add_link(prog_id, graph.graph_id, LinkType.USES_STRUCTURE)
        return full

    def update_contract(self, programme_id: str, patch: ContractUpdate) -> ContractFull | None:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            data = patch.model_dump(exclude_unset=True)
            for k, v in data.items():
                if k == "custom_tags":
                    rec.custom_tags_json = json.dumps(v)
                elif hasattr(v, "value"):
                    setattr(rec, k, v.value)
                else:
                    setattr(rec, k, v)
            rec.version += 1
            rec.content_hash = canonical_hash({
                "programme_id": rec.programme_id,
                "version": rec.version,
                "status": rec.status,
                "signed_line": rec.signed_line,
                "limit": rec.limit,
                "rol": rec.rol,
            })
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            return _record_to_full(rec)

    def update_uplifts(self, programme_id: str, rules: list[UpliftRule]) -> ContractFull | None:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            uplift = UpliftSet(**json.loads(rec.uplift_set_json))
            uplift.version += 1
            uplift.rules = rules
            uplift.content_hash = canonical_hash([r.model_dump() for r in rules])
            rec.uplift_set_json = uplift.model_dump_json()
            rec.version += 1
            rec.content_hash = canonical_hash({"programme_id": rec.programme_id, "version": rec.version, "uplift": uplift.content_hash})
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            return _record_to_full(rec)

    def promote_ilw(self, req: IlwPriceRequest, lineage: LineageRegistry) -> ContractFull:
        payload = ContractCreate(
            cedent_name="ILW Market Counterparty",
            programme_name=req.name,
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="J. Anfosso",
            broker="Howden Re",
            status=ProgrammeStatus.BOUND,
            direction=req.direction,
            structure=StructureType.ILW,
            instrument=InstrumentType.INDUSTRY_INDEX,
            placement=PlacementType.PRIVATE,
            limit=req.limit_m,
            attachment=req.trigger_bn,
            deposit_premium=round(req.limit_m * req.rol, 4),
            rol=req.rol,
            collateral_required=req.limit_m if req.direction == ContractDirection.ASSUMED else 0.0,
            currency=req.currency,
            sim_set_id=req.industry_sim_set_id,
            region_perils=req.region_perils,
            perils=[PerilCode.WS if "WS" in rp else PerilCode.EQ for rp in req.region_perils[:1]],
            custom_tags=["ILW", req.trigger_type.value],
            ilw_spec=req.model_dump(),
        )
        return self.create_contract(payload, lineage)

    def select_slice(self, req: SliceRollupRequest) -> list[ContractFull]:
        all_c = self.list_contracts()
        out: list[ContractFull] = []
        for c in all_c:
            if req.programme_ids and c.programme_id not in req.programme_ids:
                continue
            if req.statuses and c.status not in req.statuses:
                continue
            if req.directions and c.direction not in req.directions:
                continue
            if req.underwriters and c.underwriter not in req.underwriters:
                continue
            if req.offices and c.office not in req.offices:
                continue
            if req.renewal_periods and c.renewal_period not in req.renewal_periods:
                continue
            if req.region_perils and not any(rp in c.region_perils for rp in req.region_perils):
                continue
            out.append(c)
        return out

    def current_entity_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for c in self.list_contracts():
            hashes[c.programme_id] = c.content_hash
            hashes[c.uplift_set.uplift_set_id] = c.uplift_set.content_hash
            hashes[c.graph.graph_id] = c.graph.content_hash
        return hashes
