"""Tests for the unified Submission Intake Pipeline (Drop-in, Email, Folder + Confirm/Discard gate)."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _make_minimal_pdf(lines: list[str]) -> bytes:
    """Build a tiny, valid single-page PDF containing the given text lines (no external deps)."""
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    text_ops = ["BT", "/F1 10 Tf", "50 750 Td", "14 TL"]
    for idx, line in enumerate(lines):
        if idx == 0:
            text_ops.append(f"({_esc(line)}) Tj")
        else:
            text_ops.append(f"T* ({_esc(line)}) Tj")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1")

    objs: list[bytes] = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream\nendobj\n",
    ]

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for obj in objs:
        offsets.append(buf.tell())
        buf.write(obj)
    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objs) + 1}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return buf.getvalue()


def _make_sample_xlsx() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Submission"
    ws.append(["Cedent", "Apex Coastal Mutual"])
    ws.append(["Programme", "Florida Peak Cat XL 2027"])
    ws.append(["Broker", "Guy Carpenter"])
    ws.append(["Inception", "2027-01-01"])
    ws.append(["Expiry", "2027-12-31"])
    ws.append(["Currency", "USD"])
    ws.append(["Region/Peril", "US-FL-WS, US-TX-WS"])
    ws.append([])
    ws.append(["Layer", "Name", "Type", "Limit", "Attachment", "Premium", "ROL", "Line"])
    ws.append(["L1", "Primary Cat XL", "OCC_XL", 20, 40, 4.4, "22%", "15%"])
    ws.append(["L2", "Top Cat XL", "OCC_XL", 30, 60, 4.2, "14%", "10%"])
    ws.append([])
    ws.append(["Year", "Event", "Ground Up Loss", "Region Peril"])
    ws.append([2022, "Hurricane Ian", 88.5, "US-FL-WS"])
    ws.append([2017, "Hurricane Irma", 54.0, "US-FL-WS"])

    out = io.BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


def _make_yelt_csv() -> bytes:
    lines = [
        "trial_id,event_id,day_of_year,region_peril_key,loss_amount",
        "1,1001,235,US-FL-WS,18.5",
        "2,1002,260,US-FL-WS,42.0",
        "5,1003,210,US-TX-WS,27.5",
        "10,1004,280,US-FL-WS,65.0",
    ]
    return "\n".join(lines).encode("utf-8")


def _make_elt_csv() -> bytes:
    lines = [
        "event_id,annual_rate,mean_loss,sd_indep,region_peril_key",
        "5001,0.02,35.0,8.0,US-FL-WS",
        "5002,0.01,60.0,15.0,US-TX-WS",
    ]
    return "\n".join(lines).encode("utf-8")


def test_yelt_csv_routes_to_existing_ingest_and_attaches_to_draft():
    before_contracts = len(client.get("/api/contracts").json())
    res = client.post(
        "/api/intake/drop-in",
        files={"file": ("coastal_yelt.csv", _make_yelt_csv(), "text/csv")},
    )
    assert res.status_code == 200, res.text
    draft = res.json()

    assert draft["door"] == "DROP_IN"
    assert draft["status"] == "PENDING_REVIEW"
    assert draft["sourceFileType"] == "YELT"
    assert draft["simSetId"] and draft["simSetId"].startswith("sim-")
    assert len(draft["lossFiles"]) >= 1
    assert draft["overallConfidence"] >= 0.9

    # Verify sim set was registered in the existing catalog
    sim_sets = client.get("/api/sim-sets").json()
    assert any(s["simSetId"] == draft["simSetId"] for s in sim_sets)

    # Verify sketched graph wires LOSS_SOURCE -> REGION_PERIL_FILTER -> layer -> PRICING_OUTPUT
    node_types = {n["nodeType"] for n in draft["graph"]["nodes"]}
    assert "LOSS_SOURCE" in node_types
    assert "PRICING_OUTPUT" in node_types
    loss_src = next(n for n in draft["graph"]["nodes"] if n["nodeType"] == "LOSS_SOURCE")
    assert loss_src["params"]["simSetId"] == draft["simSetId"]

    # Draft only: CMS must NOT have been mutated yet
    assert len(client.get("/api/contracts").json()) == before_contracts

    # Clean up by discarding
    disc = client.post(f"/api/intake/drafts/{draft['draftId']}/discard")
    assert disc.status_code == 200
    assert disc.json()["status"] == "DISCARDED"
    assert len(client.get("/api/contracts").json()) == before_contracts


def test_elt_csv_routes_to_existing_ingest():
    res = client.post(
        "/api/intake/drop-in",
        files={"file": ("florida_elt.csv", _make_elt_csv(), "text/csv")},
    )
    assert res.status_code == 200, res.text
    draft = res.json()
    assert draft["sourceFileType"] == "ELT"
    assert draft["simSetId"] and draft["simSetId"].startswith("sim-elt-")
    assert len(draft["lossFiles"]) >= 1

    client.post(f"/api/intake/drafts/{draft['draftId']}/discard")


def test_pdf_extraction_draft_confirm_and_discard():
    pdf_bytes = _make_minimal_pdf([
        "Cedent: Pelican Bay Assurance",
        "Programme: Gulf Coast Cat XL 2027",
        "Broker: Aon Reinsurance",
        "Inception: 2027-01-01",
        "Region: Florida Wind US-FL-WS",
        "Layer 1: $25M xs $50M | Premium $4.5M | ROL 18% | Line 12%",
        "Layer 2: $40M xs $75M | Premium $4.8M | ROL 12% | Line 10%",
        "2022 Hurricane Ian: $92M",
        "2017 Hurricane Irma: $61M",
    ])
    before_contracts = len(client.get("/api/contracts").json())

    res = client.post(
        "/api/intake/drop-in",
        files={"file": ("pelican_slip.pdf", pdf_bytes, "application/pdf")},
    )
    assert res.status_code == 200, res.text
    draft = res.json()
    ext = draft["extracted"]

    assert draft["sourceFileType"] == "PDF"
    assert draft["status"] == "PENDING_REVIEW"
    assert "Pelican Bay Assurance" in ext["cedentName"]
    assert "Gulf Coast Cat XL 2027" in ext["programmeName"]
    assert ext["inceptionDate"] == "2027-01-01"
    assert "US-FL-WS" in ext["regionPerils"]
    assert len(ext["layers"]) == 2
    assert ext["layers"][0]["limit"] == 25.0
    assert ext["layers"][0]["attachment"] == 50.0
    assert ext["layers"][0]["depositPremium"] == 4.5
    assert abs(ext["layers"][0]["signedLine"] - 0.12) < 1e-4
    assert len(ext["lossHistory"]) == 2
    assert ext["lossHistory"][0]["eventName"] == "Hurricane Ian"
    assert ext["lossHistory"][0]["groundUpLossM"] == 92.0

    # Sketched graph has Loss Source, 2 layers, 2 Pricing Outputs, and Programme YELT
    nodes = draft["graph"]["nodes"]
    assert sum(1 for n in nodes if n["nodeType"] == "OCC_XL") == 2
    assert sum(1 for n in nodes if n["nodeType"] == "PRICING_OUTPUT") == 2

    # CMS untouched before confirm
    assert len(client.get("/api/contracts").json()) == before_contracts

    # Confirm writes a SUBMISSION draft programme (never BOUND)
    conf = client.post(f"/api/intake/drafts/{draft['draftId']}/confirm", json={})
    assert conf.status_code == 200, conf.text
    body = conf.json()
    assert body["action"] == "CREATED_DRAFT_PROGRAMME"
    assert body["contract"]["status"] == "SUBMISSION"
    assert body["contract"]["cedentName"] == "Pelican Bay Assurance"
    assert body["draft"]["status"] == "CONFIRMED"
    assert body["manifest"]["runType"] == "SUBMISSION_INTAKE_CONFIRM"

    # CMS and health counts stay synced
    after_contracts = client.get("/api/contracts").json()
    health = client.get("/api/health").json()
    assert len(after_contracts) == before_contracts + 1
    assert health["programmesLoaded"] == len(after_contracts)

    # Double-confirm is rejected with 409
    again = client.post(f"/api/intake/drafts/{draft['draftId']}/confirm", json={})
    assert again.status_code == 409


def test_excel_extraction_and_discard_leaves_cms_unchanged():
    before_ids = {c["programmeId"] for c in client.get("/api/contracts").json()}
    xlsx_bytes = _make_sample_xlsx()

    res = client.post(
        "/api/intake/drop-in",
        files={
            "file": (
                "apex_submission.xlsx",
                xlsx_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert res.status_code == 200, res.text
    draft = res.json()
    ext = draft["extracted"]

    assert draft["sourceFileType"] == "EXCEL"
    assert ext["cedentName"] == "Apex Coastal Mutual"
    assert ext["programmeName"] == "Florida Peak Cat XL 2027"
    assert ext["inceptionDate"] == "2027-01-01"
    assert "US-FL-WS" in ext["regionPerils"]
    assert len(ext["layers"]) == 2
    assert ext["layers"][0]["limit"] == 20.0
    assert ext["layers"][0]["attachment"] == 40.0
    assert len(ext["lossHistory"]) == 2
    assert ext["lossHistory"][0]["groundUpLossM"] == 88.5

    # Discard must leave CMS completely untouched
    disc = client.post(f"/api/intake/drafts/{draft['draftId']}/discard")
    assert disc.status_code == 200
    assert disc.json()["status"] == "DISCARDED"
    after_ids = {c["programmeId"] for c in client.get("/api/contracts").json()}
    assert after_ids == before_ids


def test_email_door_status_and_fixture_matching(monkeypatch):
    monkeypatch.delenv("ROARING_LION_EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("ROARING_LION_EMAIL_USERNAME", raising=False)
    monkeypatch.delenv("ROARING_LION_EMAIL_PASSWORD", raising=False)
    monkeypatch.setenv("ROARING_LION_SUBMISSIONS_EMAIL", "submissions@roaringlion.test")

    status = client.get("/api/intake/email/status").json()
    assert status["connected"] is False
    assert status["inboxAddress"] == "submissions@roaringlion.test"
    assert "not connected" in status["message"].lower()

    poll = client.post("/api/intake/email/poll").json()
    assert poll["connected"] is False
    assert poll["processedCount"] == 0

    # Fixture email matching existing programme prog-farmers-us-27 with a YELT attachment
    yelt_b64 = base64.b64encode(_make_yelt_csv()).decode("ascii")
    ingest = client.post(
        "/api/intake/email/ingest",
        json={
            "messageId": "fixture-msg-farmers-001",
            "sender": "broker@guycarp.example",
            "subject": "RE: prog-farmers-us-27 updated Florida YELT submission",
            "bodyText": "Please find attached updated YELT for Farmers US Cat XL.",
            "attachments": [
                {"filename": "farmers_yelt_2027.csv", "contentBase64": yelt_b64, "contentType": "text/csv"}
            ],
        },
    )
    assert ingest.status_code == 200, ingest.text
    drafts = ingest.json()
    assert len(drafts) == 1
    d = drafts[0]
    assert d["door"] == "EMAIL"
    assert d["targetProgrammeId"] == "prog-farmers-us-27"
    assert d["sourceFileType"] == "YELT"
    assert d["simSetId"] and d["simSetId"].startswith("sim-")

    # Re-ingesting the exact same messageId is deduplicated
    dup = client.post(
        "/api/intake/email/ingest",
        json={
            "messageId": "fixture-msg-farmers-001",
            "sender": "broker@guycarp.example",
            "subject": "RE: prog-farmers-us-27 updated Florida YELT submission",
            "bodyText": "Duplicate",
            "attachments": [],
        },
    )
    assert dup.status_code == 200
    assert dup.json() == []

    # Confirming attaches the simSetId to prog-farmers-us-27 WITHOUT altering its status
    orig = next(c for c in client.get("/api/contracts").json() if c["programmeId"] == "prog-farmers-us-27")
    conf = client.post(f"/api/intake/drafts/{d['draftId']}/confirm", json={"mode": "ATTACH_EXISTING"})
    assert conf.status_code == 200, conf.text
    assert conf.json()["action"] == "UPDATED_PROGRAMME"
    assert conf.json()["contract"]["programmeId"] == "prog-farmers-us-27"
    assert conf.json()["contract"]["simSetId"] == d["simSetId"]
    assert conf.json()["contract"]["status"] == orig["status"]

    # Restore original simSetId on prog-farmers-us-27 so other tests see seeded state
    client.patch("/api/contracts/prog-farmers-us-27", json={"simSetId": orig["simSetId"]})

    # Unmatched email creates a draft with targetProgrammeId=None that can be confirmed as a new SUBMISSION draft
    unmatched_pdf = base64.b64encode(
        _make_minimal_pdf([
            "Cedent: Meridian Coastal Re",
            "Programme: Southeast Wind Cat XL 2027",
            "Inception: 2027-06-01",
            "Region: Southeast Wind US-SE-WS",
            "Layer 1: $15M xs $30M | ROL 19% | Line 10%",
        ])
    ).decode("ascii")
    unmatched = client.post(
        "/api/intake/email/ingest",
        json={
            "messageId": "fixture-msg-unmatched-002",
            "sender": "submissions@broker.example",
            "subject": "New Business: Meridian Coastal Re 2027 Cat XL",
            "bodyText": "Attached slip for new cedent Meridian Coastal Re.",
            "attachments": [
                {"filename": "meridian_slip.pdf", "contentBase64": unmatched_pdf, "contentType": "application/pdf"}
            ],
        },
    )
    assert unmatched.status_code == 200, unmatched.text
    u_drafts = unmatched.json()
    assert len(u_drafts) == 1
    assert u_drafts[0]["targetProgrammeId"] is None
    assert u_drafts[0]["extracted"]["cedentName"] == "Meridian Coastal Re"
    client.post(f"/api/intake/drafts/{u_drafts[0]['draftId']}/discard")


def test_watched_folder_door_pickup_and_idempotency(tmp_path: Path, monkeypatch):
    watch_root = tmp_path / "submissions_watch"
    monkeypatch.setenv("ROARING_LION_SUBMISSIONS_FOLDER_ROOT", str(watch_root))

    # Reject path traversal
    bad = client.put(
        "/api/intake/programmes/prog-farmers-us-27/watched-folder",
        json={"watchedFolder": "../../etc"},
    )
    assert bad.status_code == 422

    # Assign a watched folder to prog-farmers-us-27
    ok = client.put(
        "/api/intake/programmes/prog-farmers-us-27/watched-folder",
        json={"watchedFolder": "farmers-2027"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["watchedFolder"] == "farmers-2027"

    prog_dir = watch_root / "farmers-2027"
    assert prog_dir.is_dir()

    # Drop an Excel submission into the watched folder
    (prog_dir / "renewal_schedule.xlsx").write_bytes(_make_sample_xlsx())

    # Scan picks it up as a FOLDER draft filed onto prog-farmers-us-27
    scan1 = client.post("/api/intake/folder/scan?programmeId=prog-farmers-us-27")
    assert scan1.status_code == 200, scan1.text
    res1 = scan1.json()
    assert res1["scannedProgrammes"] == 1
    assert res1["pickedUpCount"] == 1
    assert len(res1["drafts"]) == 1
    draft = res1["drafts"][0]
    assert draft["door"] == "FOLDER"
    assert draft["targetProgrammeId"] == "prog-farmers-us-27"
    assert draft["sourceFileType"] == "EXCEL"

    # Re-scanning skips already-picked-up file
    scan2 = client.post("/api/intake/folder/scan?programmeId=prog-farmers-us-27")
    assert scan2.status_code == 200
    assert scan2.json()["pickedUpCount"] == 0
    assert scan2.json()["skippedCount"] == 1

    # Clean up draft & folder binding
    client.post(f"/api/intake/drafts/{draft['draftId']}/discard")
    client.put("/api/intake/programmes/prog-farmers-us-27/watched-folder", json={"watchedFolder": None})
