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

    # Re-ingesting the exact same messageId returns the existing draft without creating duplicates
    before_drafts = len(client.get("/api/intake/drafts").json())
    dup = client.post(
        "/api/intake/email/ingest",
        json={
            "messageId": "fixture-msg-farmers-001",
            "sender": "broker@guycarp.example",
            "subject": "RE: prog-farmers-us-27 updated Florida YELT submission",
            "bodyText": "Duplicate",
            "attachments": [
                {"filename": "farmers_yelt_2027.csv", "contentBase64": yelt_b64, "contentType": "text/csv"}
            ],
        },
    )
    assert dup.status_code == 200
    assert len(dup.json()) == 1
    assert dup.json()[0]["draftId"] == d["draftId"]
    assert len(client.get("/api/intake/drafts").json()) == before_drafts

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


def test_poll_email_inbox_uses_body_peek_and_marks_seen_only_on_success(monkeypatch):
    """Bugbot #1: IMAP fetch must use BODY.PEEK[] and mark \\Seen only after ingest succeeds."""
    from email.message import EmailMessage

    monkeypatch.setenv("ROARING_LION_SUBMISSIONS_EMAIL", "submissions@roaringlion.test")
    monkeypatch.setenv("ROARING_LION_EMAIL_IMAP_HOST", "imap.roaringlion.test")
    monkeypatch.setenv("ROARING_LION_EMAIL_USERNAME", "submissions@roaringlion.test")
    monkeypatch.setenv("ROARING_LION_EMAIL_PASSWORD", "test-pass")

    msg_fail = EmailMessage()
    msg_fail["Message-ID"] = "<imap-fail-001@test>"
    msg_fail["From"] = "broker@example.com"
    msg_fail["Subject"] = "Oversized slip"
    msg_fail.set_content("Body")
    msg_fail.add_attachment(b"x" * 200, maintype="application", subtype="pdf", filename="oversized.pdf")

    msg_ok = EmailMessage()
    msg_ok["Message-ID"] = "<imap-ok-002@test>"
    msg_ok["From"] = "broker@example.com"
    msg_ok["Subject"] = "Valid submission"
    msg_ok.set_content("Cedent: Beacon Re\nProgramme: Beacon FL Cat XL 2027\nInception: 2027-01-01\nRegion: US-FL-WS\nLayer 1: $10M xs $20M | ROL 15%")

    raw_by_num = {b"1": msg_fail.as_bytes(), b"2": msg_ok.as_bytes()}
    fetch_commands: list[str] = []
    marked_seen: list[bytes] = []

    class FakeIMAP:
        def __init__(self, host: str, port: int):
            self.host = host
            self.port = port

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user: str, password: str):
            return ("OK", [b"Logged in"])

        def select(self, mailbox: str):
            return ("OK", [b"2"])

        def search(self, charset, criterion: str):
            assert criterion == "UNSEEN"
            return ("OK", [b"1 2"])

        def fetch(self, num: bytes, cmd: str):
            fetch_commands.append(cmd)
            return ("OK", [(b"1 (BODY[] {100})", raw_by_num[num])])

        def store(self, num: bytes, op: str, flags: str):
            if "+FLAGS" in op and "Seen" in flags:
                marked_seen.append(num)
            return ("OK", [])

    import app.services.intake as intake_mod

    monkeypatch.setattr(intake_mod.imaplib, "IMAP4_SSL", FakeIMAP)
    # 200-byte attachment fails size check while small body-only message 2 succeeds
    monkeypatch.setattr(intake_mod, "MAX_UPLOAD_BYTES", 150)

    res = client.post("/api/intake/email/poll")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["connected"] is True
    assert body["processedCount"] == 1
    assert body["skippedCount"] == 1

    # Must use BODY.PEEK[] (never RFC822)
    assert fetch_commands == ["(BODY.PEEK[])", "(BODY.PEEK[])"]
    # Failed message 1 must remain UNSEEN; successful message 2 must be marked \Seen
    assert marked_seen == [b"2"]

    for d in body["drafts"]:
        client.post(f"/api/intake/drafts/{d['draftId']}/discard")


def test_parquet_yelt_reads_actual_trial_count():
    """Bugbot #2: Parquet YELT detection must read actual trial count instead of hardcoding 10,000."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    tbl = pa.table({
        "trial_id": pa.array([1, 25_000, 50_000], type=pa.uint32()),
        "event_id": pa.array([101, 102, 103], type=pa.uint64()),
        "day_of_year": pa.array([210, 240, 265], type=pa.uint16()),
        "region_peril_key": pa.array(["US-FL-WS", "US-FL-WS", "US-TX-WS"]),
        "loss_amount": pa.array([100.0, 150.0, 250.0], type=pa.float64()),
    })
    buf = io.BytesIO()
    pq.write_table(tbl, buf)

    res = client.post(
        "/api/intake/drop-in",
        files={"file": ("cat_50k.parquet", buf.getvalue(), "application/octet-stream")},
    )
    assert res.status_code == 200, res.text
    draft = res.json()
    assert draft["sourceFileType"] == "YELT"

    sim_sets = client.get("/api/sim-sets").json()
    sim = next(s for s in sim_sets if s["simSetId"] == draft["simSetId"])
    assert sim["numTrials"] == 50_000
    # Total loss = 500.0 across 50,000 trials -> AAL = 0.01 (not 0.05 from hardcoded 10,000)
    assert sim["expectedAnnualLoss"] == 0.01

    client.post(f"/api/intake/drafts/{draft['draftId']}/discard")


def test_confirm_attach_existing_two_layers_succeeds_atomically():
    """Bugbot #3: ATTACH_EXISTING with multiple layers must unpack ContractFull properly and commit atomically."""
    # First create a clean target programme in CMS
    base_pdf = _make_minimal_pdf([
        "Cedent: Harbor MultiLayer Re",
        "Programme: Harbor Base Cat XL 2027",
        "Inception: 2027-01-01",
        "Region: US-FL-WS",
        "Layer 1: $10M xs $20M | Premium $2.0M | Line 10%",
    ])
    d0 = client.post("/api/intake/drop-in", files={"file": ("base.pdf", base_pdf, "application/pdf")}).json()
    prog_id = client.post(f"/api/intake/drafts/{d0['draftId']}/confirm", json={}).json()["contract"]["programmeId"]
    before_layers = len(client.get(f"/api/contracts/{prog_id}/layers").json())

    # Now drop a 2-layer Excel schedule targeted at that existing programme
    d1 = client.post(
        "/api/intake/drop-in",
        data={"programme_id": prog_id},
        files={
            "file": (
                "two_layers.xlsx",
                _make_sample_xlsx(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    ).json()
    assert len(d1["extracted"]["layers"]) == 2

    conf = client.post(f"/api/intake/drafts/{d1['draftId']}/confirm", json={"mode": "ATTACH_EXISTING"})
    assert conf.status_code == 200, conf.text
    body = conf.json()
    assert body["action"] == "UPDATED_PROGRAMME"
    assert body["draft"]["status"] == "CONFIRMED"
    assert body["contract"]["status"] == "SUBMISSION"

    after_layers = client.get(f"/api/contracts/{prog_id}/layers").json()
    assert len(after_layers) == before_layers + 2


def test_confirm_edited_premium_wins_over_stale_rol():
    """Bugbot #4: User-edited depositPremium on confirm must be stored in CMS (not overwritten by stale rol)."""
    pdf_bytes = _make_minimal_pdf([
        "Cedent: Summit Premium Test Re",
        "Programme: Summit Cat XL 2027",
        "Inception: 2027-01-01",
        "Region: US-FL-WS",
        "Layer 1: $25M xs $50M | Premium $4.5M | ROL 18% | Line 10%",
    ])
    draft = client.post("/api/intake/drop-in", files={"file": ("summit.pdf", pdf_bytes, "application/pdf")}).json()
    extracted = draft["extracted"]
    assert extracted["layers"][0]["depositPremium"] == 4.5
    assert extracted["layers"][0]["rol"] == 0.18

    # User edits depositPremium to 6.25 while stale rol=0.18 is still present in payload
    extracted["layers"][0]["depositPremium"] = 6.25

    conf = client.post(f"/api/intake/drafts/{draft['draftId']}/confirm", json={"extracted": extracted})
    assert conf.status_code == 200, conf.text
    prog_id = conf.json()["contract"]["programmeId"]

    layers = client.get(f"/api/contracts/{prog_id}/layers").json()
    assert len(layers) == 1
    assert abs(layers[0]["depositPremium"] - 6.25) < 1e-4
    assert abs(layers[0]["rol"] - 0.25) < 1e-4


def test_confirm_blocks_missing_region_perils_without_silent_substitution():
    """Bugbot #5: Confirm must block (422) and name missing region/peril keys instead of silently substituting."""
    # Ingest a US-FL-WS-only YELT sim set
    fl_only_csv = b"trial_id,event_id,day_of_year,region_peril_key,loss_amount\n1,1001,240,US-FL-WS,20.0\n2,1002,250,US-FL-WS,35.0\n"
    yelt_draft = client.post("/api/intake/drop-in", files={"file": ("fl_only.csv", fl_only_csv, "text/csv")}).json()
    fl_sim_id = yelt_draft["simSetId"]
    client.post(f"/api/intake/drafts/{yelt_draft['draftId']}/discard")

    # Drop a PDF referencing JP-KANTO-EQ attached to the US-FL-WS-only sim set
    pdf_bytes = _make_minimal_pdf([
        "Cedent: Nippon Mismatch Re",
        "Programme: Japan Quake XL 2027",
        "Inception: 2027-01-01",
        "Region: JP-KANTO-EQ",
        "Layer 1: $20M xs $40M | Premium $3.0M",
    ])
    draft = client.post(
        "/api/intake/drop-in",
        data={"attach_sim_set_id": fl_sim_id},
        files={"file": ("nippon.pdf", pdf_bytes, "application/pdf")},
    ).json()

    before_contracts = len(client.get("/api/contracts").json())
    conf = client.post(f"/api/intake/drafts/{draft['draftId']}/confirm", json={})
    assert conf.status_code == 422, conf.text
    assert "JP-KANTO-EQ" in conf.json()["detail"]

    # Draft remains PENDING_REVIEW and CMS is unchanged
    still = client.get(f"/api/intake/drafts/{draft['draftId']}").json()
    assert still["status"] == "PENDING_REVIEW"
    assert len(client.get("/api/contracts").json()) == before_contracts

    client.post(f"/api/intake/drafts/{draft['draftId']}/discard")


def test_email_partial_failure_retry_does_not_duplicate_first_attachment(monkeypatch):
    """Bugbot #6: Atomic dedup key ensures retrying a partially failed multi-attachment email creates no duplicates."""
    yelt_b64 = base64.b64encode(_make_yelt_csv()).decode("ascii")
    pdf_b64 = base64.b64encode(
        _make_minimal_pdf([
            "Cedent: Retry Guard Mutual",
            "Programme: Retry Guard Cat XL 2027",
            "Inception: 2027-01-01",
            "Region: US-FL-WS",
            "Layer 1: $15M xs $30M | Premium $2.5M",
        ])
    ).decode("ascii")

    # First attempt: attachment 2 is invalid/oversized? Use a bad base64 or trigger failure on second ingest_file call
    import app.services.intake as intake_mod

    orig_ingest_file = intake_mod.SubmissionIntakeService.ingest_file
    calls = {"n": 0}

    def flaky_ingest_file(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("Simulated transient failure on second attachment")
        return orig_ingest_file(self, **kwargs)

    monkeypatch.setattr(intake_mod.SubmissionIntakeService, "ingest_file", flaky_ingest_file)

    payload = {
        "messageId": "fixture-msg-partial-crash-999",
        "sender": "broker@example.com",
        "subject": "Retry Guard Mutual 2027",
        "bodyText": "Two attachments",
        "attachments": [
            {"filename": "retry_yelt.csv", "contentBase64": yelt_b64, "contentType": "text/csv"},
            {"filename": "retry_slip.pdf", "contentBase64": pdf_b64, "contentType": "application/pdf"},
        ],
    }

    fail_res = client.post("/api/intake/email/ingest", json=payload)
    assert fail_res.status_code == 422

    # Restore normal ingest_file and retry the exact same email messageId
    monkeypatch.setattr(intake_mod.SubmissionIntakeService, "ingest_file", orig_ingest_file)
    ok_res = client.post("/api/intake/email/ingest", json=payload)
    assert ok_res.status_code == 200, ok_res.text
    drafts = ok_res.json()
    assert len(drafts) == 2

    # Retry a third time: must return the exact same two draftIds and create zero extra drafts
    again_res = client.post("/api/intake/email/ingest", json=payload)
    assert again_res.status_code == 200
    assert [d["draftId"] for d in again_res.json()] == [d["draftId"] for d in drafts]

    all_matching = [
        d for d in client.get("/api/intake/drafts").json() if d["sourceRef"].endswith("Retry Guard Mutual 2027")
    ]
    assert len(all_matching) == 2

    for d in drafts:
        client.post(f"/api/intake/drafts/{d['draftId']}/discard")
