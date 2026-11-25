"""Persistent Contract Management System (CMS) Ledger & One-Click Sliced Rollup Service."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.db.database import get_session
from app.db.models import ContractNoteRecord, ContractRecord
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
from app.models.phase1 import (
    ContractCreate,
    ContractFull,
    ContractLayer,
    ContractLayerUpdate,
    ContractNoteCreate,
    ContractNoteOut,
    ContractUpdate,
    IlwPriceRequest,
    ProgrammeProfile,
    RenewalChainRow,
    RenewProgrammeRequest,
    SliceRollupRequest,
)
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
        profile=ProgrammeProfile(**json.loads(r.profile_json)) if r.profile_json else ProgrammeProfile(),
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



_LAYER_TYPES = {
    NodeType.OCC_XL: ("occLimit", "occAttachment"),
    NodeType.AGG_XL: ("aggLimit", "aggAttachment"),
    NodeType.ILW_OPTION: ("limit", "trigger"),
    NodeType.QUOTA_SHARE: ("aggCap", None),
    NodeType.AGG_QUOTA_SHARE: ("aggCap", None),
    NodeType.SURPLUS_SHARE: (None, None),
    NodeType.CASCADING_TOWER: (None, None),
}


def _opt_float(v) -> float | None:
    return None if v is None else float(v)


def graph_layers(graph: GraphSpec, default_written: float, default_signed: float) -> list[ContractLayer]:
    """Financial-terms nodes of a programme graph presented as contract layers (limits at 100%)."""
    out: list[ContractLayer] = []
    for node in graph.nodes:
        if node.node_type not in _LAYER_TYPES:
            continue
        p = node.params
        limit_key, attach_key = _LAYER_TYPES[node.node_type]
        editable = ["label", "depositPremium", "writtenLine", "signedLine"]
        if node.node_type == NodeType.CASCADING_TOWER:
            subs = p.get("layers", []) or []
            limit = sum(float(l.get("occLimit", 0) or 0) for l in subs) or None
            attachment = min((float(l.get("occAttachment", 0) or 0) for l in subs), default=None)
            premium = sum(float(l.get("depositPremium", 0) or 0) for l in subs)
            signed = float(subs[0].get("placedShare", default_signed)) if subs else default_signed
            editable = ["label", "writtenLine", "signedLine"]
        else:
            limit = _opt_float(p.get(limit_key)) if limit_key else None
            attachment = _opt_float(p.get(attach_key)) if attach_key else None
            premium = float(p.get("depositPremium", 0.0) or 0.0)
            signed = float(p.get("placedShare", default_signed))
            if limit_key:
                editable.append("limit")
            if attach_key:
                editable.append("attachment")
            if node.node_type == NodeType.OCC_XL:
                editable += ["reinstatements", "ripRate"]
        written = float(p.get("writtenLine", max(default_written, signed)))
        out.append(
            ContractLayer(
                node_id=node.node_id,
                label=node.label,
                node_type=node.node_type.value,
                limit=limit,
                attachment=attachment,
                deposit_premium=round(premium, 6),
                rol=round(premium / limit, 6) if limit else None,
                reinstatements=_opt_float(p.get("reinstatements")) if node.node_type == NodeType.OCC_XL else None,
                rip_rate=_opt_float(p.get("ripRate")) if node.node_type == NodeType.OCC_XL else None,
                written_line=written,
                signed_line=signed,
                our_limit=round(limit * signed, 6) if limit else None,
                our_premium=round(premium * signed, 6),
                editable=editable,
            )
        )
    return out


def apply_layer_updates(graph: GraphSpec, updates: list[ContractLayerUpdate]) -> GraphSpec:
    nodes = {n.node_id: n for n in graph.nodes}
    for u in updates:
        node = nodes.get(u.node_id)
        if node is None or node.node_type not in _LAYER_TYPES:
            raise KeyError(f"Layer {u.node_id} not found on this programme")
        for name, val in (("writtenLine", u.written_line), ("signedLine", u.signed_line)):
            if val is not None and not 0.0 <= val <= 1.0:
                raise ValueError(f"{node.label}: {name} must be between 0% and 100%")
        p = dict(node.params)
        limit_key, attach_key = _LAYER_TYPES[node.node_type]
        if u.label is not None and u.label.strip():
            node.label = u.label.strip()
        if node.node_type == NodeType.CASCADING_TOWER:
            if u.signed_line is not None:
                p["layers"] = [{**l, "placedShare": u.signed_line} for l in p.get("layers", []) or []]
        else:
            if u.limit is not None and limit_key:
                if u.limit <= 0:
                    raise ValueError(f"{node.label}: limit must be greater than 0")
                p[limit_key] = u.limit
            if u.attachment is not None and attach_key:
                if u.attachment < 0:
                    raise ValueError(f"{node.label}: attachment must be 0 or more")
                p[attach_key] = u.attachment
            if u.deposit_premium is not None:
                if u.deposit_premium < 0:
                    raise ValueError(f"{node.label}: premium must be 0 or more")
                p["depositPremium"] = u.deposit_premium
            if u.reinstatements is not None and node.node_type == NodeType.OCC_XL:
                p["reinstatements"] = u.reinstatements
            if u.rip_rate is not None and node.node_type == NodeType.OCC_XL:
                p["ripRate"] = u.rip_rate
            if u.signed_line is not None:
                p["placedShare"] = u.signed_line
        if u.written_line is not None:
            p["writtenLine"] = u.written_line
        node.params = p
    return graph


class ContractStore:
    """SQLAlchemy-backed CMS Contract Ledger."""

    def ensure_seeded(self, default_sim_set_id: str) -> None:
        with get_session() as db:
            if db.query(ContractRecord).count() > 0:
                return
            for idx, prog in enumerate((
                _build_farmers_programme(default_sim_set_id),
                _build_pacific_eq_programme(default_sim_set_id),
                _build_eurowind_programme(default_sim_set_id),
            ), start=1):
                is_farmers = "farmers" in prog.programme_id
                is_pacific = "pacific" in prog.programme_id
                ee_id = "ee-prog-farmers-27" if is_farmers else ("ee-prog-pacific-27" if is_pacific else None)
                profile = ProgrammeProfile(
                    unique_code=f"RL-{prog.treaty_year}-{idx:03d}",
                    program_code=f"PGM-{idx:03d}",
                    legal_entity="RoaringLion Re Transformer",
                    trading_unit="N.A. Property" if (is_farmers or is_pacific) else "International Property",
                    planning_class="Property CAT",
                    new_or_renewal="RENEWAL",
                    is_multi_year=False,
                    written_date="2026-11-18",
                    external_file_path=f"https://docs.internal/reinsurance/{prog.treaty_year}/{prog.programme_id}",
                    cedent_group=prog.cedent_name,
                    cedent_country="USA" if is_farmers else ("USA / JP" if is_pacific else "DE"),
                    cedent_credit_rating="AM Best A" if is_farmers else ("AM Best A-" if is_pacific else "S&P A+"),
                    broker_group=prog.broker,
                    closing_broker=prog.broker,
                    broker_contact="Lead Treaty Desk",
                    pricing_analyst="Cat Modeling Desk",
                    offered_line=prog.written_line,
                    estimated_signed_pct=1.0,
                    limit_basis="UNL",
                    claim_trigger="LOSSES_OCCURRING",
                    loss_event_type="1ST_LOSS",
                    reinstatement_basis="PRO_RATA_AMOUNT",
                    inuring_note="Underlying QS inures prior to 1st Cat XL" if is_farmers else "Clean occurrence tower",
                    economic_inflation_pct=3.5 if is_farmers else 2.5,
                    social_inflation_pct=1.2 if is_farmers else 0.0,
                    gearing_effect_pct=14.5 if is_farmers else 6.0,
                    rarc_exposure_pct=1.8 if is_farmers else 0.6,
                    rarc_layer_pct=-2.1 if is_farmers else 0.4,
                    rarc_price_pct=6.4 if is_farmers else (4.2 if is_pacific else 3.1),
                    rarc_total_pct=6.1 if is_farmers else (5.2 if is_pacific else 4.1),
                    prior_year_limit_m=round(prog.limit * 0.94, 2),
                    prior_year_premium_m=round(prog.deposit_premium * 0.91, 2),
                    prior_year_rol_pct=round(prog.rol * 94.0, 2),
                    prior_year_signed_line=prog.signed_line,
                )
                chash = canonical_hash(prog.model_dump())
                chain_key = "chain-prog-farmers-us" if is_farmers else f"chain-{prog.programme_id}"
                prior_id = "prog-farmers-us-26" if is_farmers else None
                rec = ContractRecord(
                    programme_id=prog.programme_id,
                    chain_id=chain_key,
                    prior_contract_id=prior_id,
                    cedent_name=prog.cedent_name,
                    programme_name=prog.programme_name,
                    treaty_year=prog.treaty_year,
                    renewal_period=prog.renewal_period,
                    office=prog.office,
                    underwriter=prog.underwriter,
                    broker=prog.broker,
                    fronting_carrier="RoaringLion Re Transformer",
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
                    attachment=80.0 if is_farmers else (70.0 if is_pacific else 45.0),
                    deposit_premium=prog.deposit_premium,
                    rol=prog.rol,
                    technical_rol=round(prog.rol * 0.92, 4),
                    brokerage_pct=0.10,
                    fronting_fee_pct=0.02,
                    collateral_required=prog.collateral_required,
                    currency=prog.currency,
                    sim_set_id=prog.sim_set_id,
                    exposure_eclipse_programme_id=ee_id,
                    region_perils_json=json.dumps(prog.region_perils),
                    perils_json=json.dumps([p.value for p in prog.perils]),
                    custom_tags_json=json.dumps(["Peak" if "US-FL-WS" in prog.region_perils else "Diversifier"]),
                    uplift_set_json=prog.uplift_set.model_dump_json(),
                    graph_json=prog.graph.model_dump_json(),
                    profile_json=profile.model_dump_json(),
                    version=1,
                    content_hash=chash,
                )
                db.add(rec)
                if is_farmers:
                    prior_profile = ProgrammeProfile(
                        unique_code="RL-2026-001",
                        program_code="PGM-001",
                        legal_entity="RoaringLion Re Transformer",
                        trading_unit="N.A. Property",
                        planning_class="Property CAT",
                        new_or_renewal="RENEWAL",
                        written_date="2025-11-20",
                        cedent_group=prog.cedent_name,
                        cedent_country="USA",
                        cedent_credit_rating="AM Best A",
                        broker_group=prog.broker,
                        closing_broker=prog.broker,
                        broker_contact="Lead Treaty Desk",
                        pricing_analyst="Cat Modeling Desk",
                        offered_line=1.0,
                        estimated_signed_pct=1.0,
                        limit_basis="UNL",
                        claim_trigger="LOSSES_OCCURRING",
                        loss_event_type="1ST_LOSS",
                        reinstatement_basis="PRO_RATA_AMOUNT",
                        inuring_note="Underlying QS inures prior to 1st Cat XL",
                        economic_inflation_pct=3.0,
                        rarc_total_pct=4.5,
                    )
                    prior_uplift = prog.uplift_set.model_copy(
                        update={"uplift_set_id": "upl-farmers-us-26", "name": "Farmers US 2026 House Uplift"}
                    )
                    prior_graph = prog.graph.model_copy(
                        update={"graph_id": "grp-farmers-us-26", "name": "Farmers US Wind Tower 2026"}
                    )
                    db.add(
                        ContractRecord(
                            programme_id="prog-farmers-us-26",
                            chain_id="chain-prog-farmers-us",
                            prior_contract_id=None,
                            cedent_name=prog.cedent_name,
                            programme_name="US Wind Occ + Agg Tower (2026 Expiring)",
                            treaty_year=2026,
                            renewal_period="J26",
                            office=prog.office,
                            underwriter=prog.underwriter,
                            broker=prog.broker,
                            fronting_carrier="RoaringLion Re Transformer",
                            collateral_cell="Cell-J26",
                            status=ProgrammeStatus.EXPIRED.value,
                            direction=ContractDirection.ASSUMED.value,
                            structure=StructureType.OCC_XL.value,
                            instrument=InstrumentType.UNL.value,
                            placement=PlacementType.OPEN_MARKET.value,
                            inception_date="2026-01-01",
                            expiry_date="2026-12-31",
                            hours_clause=72,
                            authorized_line=1.0,
                            written_line=1.0,
                            signed_line=1.0,
                            limit=80.0,
                            attachment=75.0,
                            deposit_premium=17.6,
                            rol=0.22,
                            technical_rol=0.202,
                            brokerage_pct=0.10,
                            fronting_fee_pct=0.02,
                            collateral_required=80.0,
                            currency=prog.currency,
                            sim_set_id=prog.sim_set_id,
                            exposure_eclipse_programme_id=ee_id,
                            region_perils_json=json.dumps(prog.region_perils),
                            perils_json=json.dumps([p.value for p in prog.perils]),
                            custom_tags_json=json.dumps(["Peak", "Expiring"]),
                            uplift_set_json=prior_uplift.model_dump_json(),
                            graph_json=prior_graph.model_dump_json(),
                            profile_json=prior_profile.model_dump_json(),
                            version=1,
                            content_hash=canonical_hash({"id": "prog-farmers-us-26", "year": 2026}),
                        )
                    )
                    db.add(
                        ContractNoteRecord(
                            note_id="note-seed-farmers-1",
                            programme_id=prog.programme_id,
                            cedent_name=prog.cedent_name,
                            author=prog.underwriter,
                            category="UNDERWRITING",
                            body="FL Wind house uplift +10% applied (Adjusted view). Attachment raised +5m YoY; risk-adjusted rate change +6.1% meets fund hurdle.",
                        )
                    )
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
            profile_json=(payload.profile or ProgrammeProfile(unique_code=f"RL-{payload.treaty_year}-{prog_id[-6:].upper()}", cedent_group=payload.cedent_name)).model_dump_json(),
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
                elif k == "profile" and v is not None:
                    rec.profile_json = ProgrammeProfile(**v).model_dump_json()
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

    def update_layers(self, programme_id: str, updates: list[ContractLayerUpdate]) -> ContractFull | None:
        from app.engine.slice_runner import validate_dag_acyclic

        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            graph = GraphSpec(**json.loads(rec.graph_json))
            graph = apply_layer_updates(graph, updates)
            validate_dag_acyclic(graph)
            graph.version += 1
            graph.content_hash = canonical_hash({
                "nodes": [n.model_dump() for n in graph.nodes],
                "edges": [e.model_dump() for e in graph.edges],
            })
            rec.graph_json = graph.model_dump_json()

            layers = [l for l in graph_layers(graph, rec.written_line, rec.signed_line) if l.limit]
            total_limit = sum(l.limit or 0.0 for l in layers)
            if total_limit > 0:
                rec.limit = round(total_limit, 6)
                rec.deposit_premium = round(sum(l.deposit_premium for l in layers), 6)
                rec.rol = round(rec.deposit_premium / total_limit, 6)
                rec.written_line = round(sum((l.limit or 0) * l.written_line for l in layers) / total_limit, 6)
                rec.signed_line = round(sum((l.limit or 0) * l.signed_line for l in layers) / total_limit, 6)
                attachments = [l.attachment for l in layers if l.attachment is not None]
                if attachments:
                    rec.attachment = min(attachments)

            rec.version += 1
            rec.content_hash = canonical_hash({"programme_id": rec.programme_id, "version": rec.version, "graph": graph.content_hash})
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            return _record_to_full(rec)

    def update_graph(self, programme_id: str, graph: GraphSpec) -> ContractFull | None:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            old_graph = GraphSpec(**json.loads(rec.graph_json))
            graph.version = old_graph.version + 1
            graph.content_hash = canonical_hash({
                "nodes": [n.model_dump() for n in graph.nodes],
                "edges": [e.model_dump() for e in graph.edges],
            })
            rec.graph_json = graph.model_dump_json()
            rec.version += 1
            rec.content_hash = canonical_hash({"programme_id": rec.programme_id, "version": rec.version, "graph": graph.content_hash})
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            return _record_to_full(rec)

    def bind_deal(self, programme_id: str, lineage: LineageRegistry) -> tuple[ContractFull, str] | None:
        from app.models.domain import BreadcrumbStep

        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            rec.status = ProgrammeStatus.BOUND.value
            rec.version += 1
            rec.content_hash = canonical_hash({"programme_id": rec.programme_id, "version": rec.version, "status": "BOUND"})
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            full = _record_to_full(rec)

        manifest = lineage.record_manifest(
            run_type="DEAL_BIND",
            input_hashes={"contract": full.content_hash, "graph": full.graph.content_hash, "uplift": full.uplift_set.content_hash},
            parameters={"programme_id": programme_id, "signed_line": full.signed_line, "rol": full.rol},
            output_payload={"status": "BOUND", "version": full.version},
            breadcrumb=[
                BreadcrumbStep(entity_type="PROGRAMME", entity_id=full.programme_id, label=f"{full.cedent_name} — {full.programme_name}", version=f"v{full.version}", content_hash=full.content_hash),
                BreadcrumbStep(entity_type="DAG_GRAPH", entity_id=full.graph.graph_id, label=full.graph.name, version=f"v{full.graph.version}", content_hash=full.graph.content_hash),
            ],
        )
        lineage.add_link(full.programme_id, manifest.manifest_id, LinkType.BOUND_AS)
        return full, manifest.manifest_id

    def promote_ilw(self, req: IlwPriceRequest, lineage: LineageRegistry) -> ContractFull:
        payload = ContractCreate(
            cedent_name="ILW Market Counterparty",
            programme_name=req.name,
            treaty_year=2027,
            renewal_period="J27",
            office="BDA",
            underwriter="Lead Underwriter",
            broker="Broker C",
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

    def list_notes(self, programme_id: str, scope: str = "programme") -> list[ContractNoteOut]:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return []
            q = db.query(ContractNoteRecord)
            if scope == "cedent":
                q = q.filter(ContractNoteRecord.cedent_name == rec.cedent_name)
            else:
                q = q.filter(ContractNoteRecord.programme_id == programme_id)
            rows = q.order_by(ContractNoteRecord.created_at.desc()).all()
            return [
                ContractNoteOut(
                    note_id=n.note_id,
                    programme_id=n.programme_id,
                    cedent_name=n.cedent_name,
                    author=n.author,
                    category=n.category,
                    body=n.body,
                    created_at=n.created_at,
                )
                for n in rows
            ]

    def add_note(self, programme_id: str, payload: ContractNoteCreate) -> ContractNoteOut | None:
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None
            note = ContractNoteRecord(
                note_id=f"note-{uuid.uuid4().hex[:8]}",
                programme_id=programme_id,
                cedent_name=rec.cedent_name,
                author=payload.author.strip() or "Lead Underwriter",
                category=payload.category.upper(),
                body=payload.body.strip(),
            )
            db.add(note)
            db.commit()
            db.refresh(note)
            return ContractNoteOut(
                note_id=note.note_id,
                programme_id=note.programme_id,
                cedent_name=note.cedent_name,
                author=note.author,
                category=note.category,
                body=note.body,
                created_at=note.created_at,
            )

    def link_prior_programme(
        self,
        programme_id: str,
        prior_contract_id: str | None,
        lineage: LineageRegistry,
    ) -> ContractFull | None:
        """Link or unlink a programme to its prior underwriting year, unifying chain_id and copying prior-year metrics."""
        with get_session() as db:
            rec = db.get(ContractRecord, programme_id)
            if not rec:
                return None

            if prior_contract_id is None or prior_contract_id == "":
                rec.prior_contract_id = None
                rec.version += 1
                rec.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(rec)
                return _record_to_full(rec)

            if prior_contract_id == programme_id:
                raise ValueError("A programme cannot link to itself as prior year")

            prior = db.get(ContractRecord, prior_contract_id)
            if not prior:
                raise KeyError(f"Prior programme not found: {prior_contract_id}")

            # Cycle check: walk prior's ancestors to ensure programme_id is not already an ancestor
            cursor = prior.prior_contract_id
            seen = {programme_id, prior_contract_id}
            while cursor:
                if cursor in seen:
                    raise ValueError("Linking these programmes would create a renewal cycle")
                seen.add(cursor)
                anc = db.get(ContractRecord, cursor)
                cursor = anc.prior_contract_id if anc else None

            old_chain = rec.chain_id
            target_chain = prior.chain_id or f"chain-{prior.programme_id}"
            prior.chain_id = target_chain
            rec.prior_contract_id = prior_contract_id
            rec.chain_id = target_chain

            # Also bring any descendants of rec onto the unified chain_id
            if old_chain and old_chain != target_chain:
                for desc in db.query(ContractRecord).filter(ContractRecord.chain_id == old_chain).all():
                    desc.chain_id = target_chain

            prof = ProgrammeProfile(**json.loads(rec.profile_json)) if rec.profile_json else ProgrammeProfile()
            prof.prior_year_limit_m = prior.limit
            prof.prior_year_premium_m = prior.deposit_premium
            prof.prior_year_rol_pct = round(prior.rol * 100.0, 2)
            prof.prior_year_signed_line = prior.signed_line
            rec.profile_json = prof.model_dump_json()

            rec.version += 1
            rec.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(rec)
            out = _record_to_full(rec)

        lineage.add_link(programme_id, prior_contract_id, LinkType.SUPERSEDES)
        return out

    def renew_programme(
        self,
        programme_id: str,
        req: RenewProgrammeRequest,
        lineage: LineageRegistry,
    ) -> ContractFull | None:
        """Clone an existing programme into the next underwriting year, linked via prior_contract_id + shared chain_id."""
        with get_session() as db:
            src = db.get(ContractRecord, programme_id)
            if not src:
                return None

            next_year = req.treaty_year or (src.treaty_year + 1)
            next_period = req.renewal_period
            if not next_period:
                yr_short = str(next_year)[-2:]
                prefix = "".join(ch for ch in src.renewal_period if ch.isalpha()) or "J"
                next_period = f"{prefix}{yr_short}"

            new_id = f"prog-{uuid.uuid4().hex[:8]}"
            chain_id = src.chain_id or f"chain-{src.programme_id}"
            src.chain_id = chain_id

            new_limit = req.limit if req.limit is not None else src.limit
            new_att = req.attachment if req.attachment is not None else src.attachment
            new_rol = req.rol if req.rol is not None else src.rol
            new_signed = req.signed_line if req.signed_line is not None else src.signed_line
            new_prem = round(new_limit * new_rol, 4)

            src_prof = ProgrammeProfile(**json.loads(src.profile_json)) if src.profile_json else ProgrammeProfile()
            new_prof = src_prof.model_copy(
                update={
                    "unique_code": f"RL-{next_year}-{new_id[-4:].upper()}",
                    "new_or_renewal": "RENEWAL",
                    "prior_year_limit_m": src.limit,
                    "prior_year_premium_m": src.deposit_premium,
                    "prior_year_rol_pct": round(src.rol * 100.0, 2),
                    "prior_year_signed_line": src.signed_line,
                    "rarc_total_pct": req.rarc_total_pct if req.rarc_total_pct is not None else src_prof.rarc_total_pct,
                }
            )

            src_uplift = UpliftSet(**json.loads(src.uplift_set_json))
            src_uplift.uplift_set_id = f"upl-{new_id}"
            src_uplift.version = 1

            src_graph = GraphSpec(**json.loads(src.graph_json))
            src_graph.graph_id = f"grp-{new_id}"
            src_graph.version = 1
            src_graph.content_hash = canonical_hash({
                "nodes": [n.model_dump() for n in src_graph.nodes],
                "edges": [e.model_dump() for e in src_graph.edges],
            })

            base_name = src.programme_name.replace(f" ({src.treaty_year} Expiring)", "").strip()
            rec = ContractRecord(
                programme_id=new_id,
                chain_id=chain_id,
                prior_contract_id=src.programme_id,
                cedent_name=src.cedent_name,
                programme_name=base_name,
                treaty_year=next_year,
                renewal_period=next_period,
                office=src.office,
                underwriter=src.underwriter,
                secondary_underwriter=src.secondary_underwriter,
                broker=src.broker,
                fronting_carrier=src.fronting_carrier,
                collateral_cell=f"Cell-{next_period}",
                status=ProgrammeStatus.QUOTED.value,
                direction=src.direction,
                structure=src.structure,
                instrument=src.instrument,
                placement=src.placement,
                inception_date=f"{next_year}-01-01",
                expiry_date=f"{next_year}-12-31",
                hours_clause=src.hours_clause,
                authorized_line=src.authorized_line,
                written_line=new_signed,
                signed_line=new_signed,
                limit=new_limit,
                attachment=new_att,
                deposit_premium=new_prem,
                rol=new_rol,
                technical_rol=src.technical_rol,
                brokerage_pct=src.brokerage_pct,
                fronting_fee_pct=src.fronting_fee_pct,
                collateral_required=new_limit,
                currency=src.currency,
                sim_set_id=src.sim_set_id,
                exposure_eclipse_programme_id=src.exposure_eclipse_programme_id,
                region_perils_json=src.region_perils_json,
                perils_json=src.perils_json,
                custom_tags_json=src.custom_tags_json,
                uplift_set_json=src_uplift.model_dump_json(),
                graph_json=src_graph.model_dump_json(),
                ilw_spec_json=src.ilw_spec_json,
                profile_json=new_prof.model_dump_json(),
                version=1,
                content_hash=canonical_hash({"id": new_id, "prior": src.programme_id, "year": next_year}),
            )
            db.add(rec)
            db.commit()
            db.refresh(rec)
            out = _record_to_full(rec)

        lineage.add_link(new_id, programme_id, LinkType.SUPERSEDES)
        lineage.add_link(new_id, out.sim_set_id, LinkType.USES_LOSSES)
        lineage.add_link(new_id, out.graph.graph_id, LinkType.USES_STRUCTURE)
        return out

    def get_renewal_chain(self, programme_id: str) -> list[RenewalChainRow]:
        """Return all programmes in the YoY renewal chain ordered newest year first, with YoY deltas."""
        with get_session() as db:
            target = db.get(ContractRecord, programme_id)
            if not target:
                return []

            # Collect every record sharing chain_id OR connected via prior_contract_id links
            chain_recs: dict[str, ContractRecord] = {target.programme_id: target}
            if target.chain_id:
                for r in db.query(ContractRecord).filter(ContractRecord.chain_id == target.chain_id).all():
                    chain_recs[r.programme_id] = r

            # Walk backward and forward links defensively
            changed = True
            while changed:
                changed = False
                for r in list(chain_recs.values()):
                    if r.prior_contract_id and r.prior_contract_id not in chain_recs:
                        p = db.get(ContractRecord, r.prior_contract_id)
                        if p:
                            chain_recs[p.programme_id] = p
                            changed = True
                for child in db.query(ContractRecord).filter(ContractRecord.prior_contract_id.in_(list(chain_recs.keys()))).all():
                    if child.programme_id not in chain_recs:
                        chain_recs[child.programme_id] = child
                        changed = True

            ordered_asc = sorted(chain_recs.values(), key=lambda r: (r.treaty_year, r.programme_id))
            by_id = {r.programme_id: r for r in ordered_asc}

            rows: list[RenewalChainRow] = []
            for idx, r in enumerate(ordered_asc):
                prof = ProgrammeProfile(**json.loads(r.profile_json)) if r.profile_json else ProgrammeProfile()
                prior_rec = by_id.get(r.prior_contract_id) if r.prior_contract_id else (ordered_asc[idx - 1] if idx > 0 else None)
                yoy_lim = round(r.limit - prior_rec.limit, 2) if prior_rec else None
                yoy_rol = round((r.rol - prior_rec.rol) * 100.0, 2) if prior_rec else None
                yoy_prem = round(r.deposit_premium - prior_rec.deposit_premium, 2) if prior_rec else None
                rows.append(
                    RenewalChainRow(
                        programme_id=r.programme_id,
                        unique_code=prof.unique_code or r.programme_id,
                        programme_name=r.programme_name,
                        treaty_year=r.treaty_year,
                        renewal_period=r.renewal_period,
                        status=r.status,
                        prior_contract_id=r.prior_contract_id,
                        limit=r.limit,
                        attachment=r.attachment,
                        deposit_premium=r.deposit_premium,
                        rol_pct=round(r.rol * 100.0, 2),
                        signed_line=r.signed_line,
                        rarc_total_pct=prof.rarc_total_pct,
                        yoy_limit_delta_m=yoy_lim,
                        yoy_rol_delta_pts=yoy_rol,
                        yoy_premium_delta_m=yoy_prem,
                        is_current=(r.programme_id == programme_id),
                    )
                )
            return list(reversed(rows))

    def current_entity_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for c in self.list_contracts():
            hashes[c.programme_id] = c.content_hash
            hashes[c.uplift_set.uplift_set_id] = c.uplift_set.content_hash
            hashes[c.graph.graph_id] = c.graph.content_hash
        return hashes
