"""Bounded, streaming-safe parsers for submission intake (YELT/ELT, PDF, Excel, Text).

Heavy document libraries (`pypdf`, `openpyxl`) are imported lazily inside their parser
functions so startup RSS on 512 MB containers stays unchanged.
"""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.engine.sim_generator import REGION_PERILS
from app.models.intake import (
    IntakeExtractedData,
    IntakeFileKind,
    IntakeLayerDraft,
    IntakeLossHistoryRow,
)
from app.models.phase1 import EltRowInput

MAX_UPLOAD_MB = int(os.getenv("ROARING_LION_INTAKE_MAX_MB", "25"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_PDF_PAGES = 40
MAX_EXCEL_SHEETS = 12
MAX_EXCEL_ROWS_PER_SHEET = 2_000
MAX_EXCEL_COLS = 30
MAX_TEXT_CHARS = 120_000
MAX_ELT_ROWS = 50_000
MIN_YELT_TRIALS = 1
MAX_YELT_TRIALS = 1_000_000

_TRIAL_COLS = {"trial_id", "trial", "sim_year", "simulation_year", "year_id"}
_EVENT_COLS = {"event_id", "eventid", "event", "evt_id"}
_LOSS_COLS = {"loss_amount", "loss", "gross_loss", "ground_up_loss", "gu_loss", "amount"}
_DAY_COLS = {"day_of_year", "day", "doy", "event_day"}
_RP_COLS = {"region_peril_key", "region_peril", "regionperil", "peril_region", "zone_peril"}
_RATE_COLS = {"annual_rate", "rate", "frequency", "freq", "annual_frequency"}
_MEAN_COLS = {"mean_loss", "mean", "expected_loss", "avg_loss", "loss_mean"}
_SD_INDEP_COLS = {"sd_indep", "sdi", "std_indep", "sd_independent"}
_SD_CORR_COLS = {"sd_corr", "sdc", "std_corr", "sd_correlated"}
_MAX_LOSS_COLS = {"max_loss", "exp_value", "exposure", "max_payout"}

_CANONICAL_RP_SET = set(REGION_PERILS)

_REGION_PERIL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bUS-FL-TRI-WS\b", re.I), "US-FL-TRI-WS"),
    (re.compile(r"\bUS-FL-WS\b|florida\s+(?:wind|hurricane|named\s+storm|cat)", re.I), "US-FL-WS"),
    (re.compile(r"\bUS-TX-WS\b|texas\s+(?:wind|hurricane|gulf)", re.I), "US-TX-WS"),
    (re.compile(r"\bUS-SE-WS\b|southeast\s+(?:wind|hurricane)", re.I), "US-SE-WS"),
    (re.compile(r"\bUS-NE-WS\b|northeast\s+(?:wind|hurricane|storm)", re.I), "US-NE-WS"),
    (re.compile(r"\bUS-CA-EQ\b|california\s+(?:quake|earthquake|eq)", re.I), "US-CA-EQ"),
    (re.compile(r"\bUS-PNW-EQ\b|pacific\s+northwest\s+(?:quake|earthquake|eq)", re.I), "US-PNW-EQ"),
    (re.compile(r"\bUS-MW-CS\b|midwest\s+(?:convective|scs|hail|tornado)", re.I), "US-MW-CS"),
    (re.compile(r"\bUS-CA-WF\b|california\s+wildfire", re.I), "US-CA-WF"),
    (re.compile(r"\bJP-KANTO-EQ\b|(?:japan|tokyo|kanto)\s+(?:quake|earthquake|eq)", re.I), "JP-KANTO-EQ"),
    (re.compile(r"\bJP-TY-WS\b|japan\s+(?:typhoon|wind)", re.I), "JP-TY-WS"),
    (re.compile(r"\bEU-DE-WS\b|(?:europe|european|germany)\s+(?:wind|windstorm)", re.I), "EU-DE-WS"),
    (re.compile(r"\bEU-UK-FL\b|(?:uk|europe)\s+flood", re.I), "EU-UK-FL"),
]

_MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


@dataclass
class DetectedYeltSpec:
    trial_col: str
    event_col: str
    loss_col: str
    day_col: str | None
    region_peril_col: str | None
    default_region_peril: str = "US-FL-WS"
    num_trials: int = 10_000
    currency: str = "USD"


@dataclass
class DetectedEltSpec:
    rows: list[EltRowInput]
    currency: str = "USD"
    num_trials: int = 10_000


@dataclass
class ParsedSubmissionFile:
    file_kind: IntakeFileKind
    yelt_spec: DetectedYeltSpec | None = None
    elt_spec: DetectedEltSpec | None = None
    extracted: IntakeExtractedData = field(default_factory=IntakeExtractedData)
    overall_confidence: float = 0.5


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _find_col(col_map: dict[str, str], candidates: set[str]) -> str | None:
    for c in candidates:
        if c in col_map:
            return col_map[c]
    return None


def _detect_parquet_yelt(raw_bytes: bytes) -> DetectedYeltSpec:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(io.BytesIO(raw_bytes))
    names = list(pf.schema_arrow.names)
    col_map = {_norm_col(n): n for n in names}
    trial_col = _find_col(col_map, _TRIAL_COLS) or "trial_id"
    event_col = _find_col(col_map, _EVENT_COLS) or "event_id"
    loss_col = _find_col(col_map, _LOSS_COLS) or "loss_amount"
    day_col = _find_col(col_map, _DAY_COLS)
    rp_col = _find_col(col_map, _RP_COLS)

    max_trial = 0
    if trial_col in names:
        trial_idx = names.index(trial_col)
        stats_found = False
        for rg_i in range(pf.metadata.num_row_groups):
            col_meta = pf.metadata.row_group(rg_i).column(trial_idx)
            if col_meta.is_stats_set and col_meta.statistics is not None and col_meta.statistics.has_min_max:
                rg_max = col_meta.statistics.max
                if rg_max is not None:
                    stats_found = True
                    if int(rg_max) > max_trial:
                        max_trial = int(rg_max)
        if not stats_found and pf.metadata.num_rows > 0:
            tbl = pf.read(columns=[trial_col])
            col_max = pc.max(tbl[trial_col]).as_py()
            if col_max is not None:
                max_trial = int(col_max)
            del tbl
            pa.default_memory_pool().release_unused()

    default_rp = "US-FL-WS"
    if rp_col and rp_col in names and pf.metadata.num_rows > 0:
        first_batch = next(pf.iter_batches(batch_size=1, columns=[rp_col]), None)
        if first_batch is not None and first_batch.num_rows > 0:
            rp_val = first_batch.column(0)[0].as_py()
            if rp_val:
                default_rp = str(rp_val).strip()
        pa.default_memory_pool().release_unused()

    if max_trial > MAX_YELT_TRIALS:
        raise ValueError(
            f"YELT Parquet trial_id ({max_trial:,}) exceeds the maximum allowed num_trials ({MAX_YELT_TRIALS:,})."
        )
    num_trials = max_trial if max_trial >= MIN_YELT_TRIALS else 10_000
    return DetectedYeltSpec(
        trial_col=trial_col,
        event_col=event_col,
        loss_col=loss_col,
        day_col=day_col,
        region_peril_col=rp_col,
        default_region_peril=default_rp,
        num_trials=num_trials,
    )


def _detect_csv_modelling(raw_bytes: bytes) -> tuple[IntakeFileKind | None, DetectedYeltSpec | None, DetectedEltSpec | None]:
    """Inspect CSV header in bounded memory; detect YELT or ELT before any LLM/text extraction."""
    sample = raw_bytes[:65_536].decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(sample))
    try:
        header = next(reader)
    except StopIteration:
        return None, None, None
    if not header or len(header) < 3:
        return None, None, None

    col_map = {_norm_col(h): h for h in header if h and h.strip()}
    trial_col = _find_col(col_map, _TRIAL_COLS)
    event_col = _find_col(col_map, _EVENT_COLS)
    loss_col = _find_col(col_map, _LOSS_COLS)
    rate_col = _find_col(col_map, _RATE_COLS)
    mean_col = _find_col(col_map, _MEAN_COLS)
    day_col = _find_col(col_map, _DAY_COLS)
    rp_col = _find_col(col_map, _RP_COLS)

    # YELT: has trial_id + event_id + loss_amount
    if trial_col and event_col and loss_col:
        # Estimate num_trials by streaming just the trial column without loading full file into polars yet
        max_trial = 0
        default_rp = "US-FL-WS"
        text_stream = io.StringIO(raw_bytes.decode("utf-8-sig", errors="replace"))
        dict_reader = csv.DictReader(text_stream)
        for idx, row in enumerate(dict_reader):
            if idx >= 200_000:
                break
            try:
                t_val = int(float(row.get(trial_col) or 0))
                if t_val > MAX_YELT_TRIALS:
                    raise ValueError(
                        f"YELT CSV trial_id ({t_val:,}) exceeds the maximum allowed num_trials ({MAX_YELT_TRIALS:,})."
                    )
                if t_val > max_trial:
                    max_trial = t_val
            except (TypeError, ValueError) as exc:
                if "exceeds the maximum allowed num_trials" in str(exc):
                    raise
                pass
            if idx == 0 and rp_col and row.get(rp_col):
                default_rp = str(row[rp_col]).strip()
        num_trials = max(max_trial, 100) if max_trial >= MIN_YELT_TRIALS else 10_000
        if not (MIN_YELT_TRIALS <= num_trials <= MAX_YELT_TRIALS):
            raise ValueError(
                f"YELT CSV num_trials ({num_trials:,}) must be between {MIN_YELT_TRIALS} and {MAX_YELT_TRIALS:,}."
            )
        return (
            IntakeFileKind.YELT,
            DetectedYeltSpec(
                trial_col=trial_col,
                event_col=event_col,
                loss_col=loss_col,
                day_col=day_col,
                region_peril_col=rp_col,
                default_region_peril=default_rp,
                num_trials=num_trials,
            ),
            None,
        )

    # ELT: has event_id + annual_rate + mean_loss (or loss)
    if event_col and rate_col and (mean_col or loss_col):
        val_col = mean_col or loss_col
        assert val_col is not None
        sd_i_col = _find_col(col_map, _SD_INDEP_COLS)
        sd_c_col = _find_col(col_map, _SD_CORR_COLS)
        max_col = _find_col(col_map, _MAX_LOSS_COLS)
        rows: list[EltRowInput] = []
        text_stream = io.StringIO(raw_bytes.decode("utf-8-sig", errors="replace"))
        dict_reader = csv.DictReader(text_stream)
        for idx, row in enumerate(dict_reader):
            if idx >= MAX_ELT_ROWS:
                break
            try:
                ev_id = int(float(row[event_col]))
                rate = float(row[rate_col])
                mean = float(row[val_col])
            except (TypeError, ValueError, KeyError):
                continue
            if rate <= 0 or mean < 0:
                continue
            sdi = _safe_float(row.get(sd_i_col), 0.0) if sd_i_col else 0.0
            sdc = _safe_float(row.get(sd_c_col), 0.0) if sd_c_col else 0.0
            mx = _safe_float(row.get(max_col), None) if max_col else None
            rp = str(row.get(rp_col) or "US-FL-WS").strip() if rp_col else "US-FL-WS"
            day = int(_safe_float(row.get(day_col), 240.0) or 240) if day_col else 240
            rows.append(
                EltRowInput(
                    event_id=ev_id,
                    annual_rate=rate,
                    mean_loss=mean,
                    sd_indep=sdi or 0.0,
                    sd_corr=sdc or 0.0,
                    max_loss=mx,
                    region_peril_key=rp or "US-FL-WS",
                    day_of_year=max(1, min(365, day)),
                )
            )
        if rows:
            return IntakeFileKind.ELT, None, DetectedEltSpec(rows=rows, num_trials=10_000)

    return None, None, None


def _safe_float(val: Any, default: float | None = 0.0) -> float | None:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "").replace("$", "").replace("€", "").replace("£", "")
    if not s:
        return default
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*([mMbBkK%])?$", s)
    if not m:
        return default
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit == "b":
        return num * 1000.0
    if unit == "k":
        return num / 1000.0
    return num


def _to_millions(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        return round(v / 1_000_000.0, 4) if abs(v) >= 100_000 else round(v, 4)
    s = str(raw).strip().replace(",", "")
    if not s:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(m|mm|mn|million|b|bn|billion|k)?", s, re.I)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit in ("b", "bn", "billion"):
        return round(num * 1000.0, 4)
    if unit == "k":
        return round(num / 1000.0, 4)
    if unit in ("m", "mm", "mn", "million"):
        return round(num, 4)
    return round(num / 1_000_000.0, 4) if abs(num) >= 100_000 else round(num, 4)


def _to_fraction(raw: Any, default: float | None = None) -> float | None:
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        v = float(raw)
        return round(v / 100.0, 4) if v > 1.0 else round(v, 4)
    s = str(raw).strip()
    if not s:
        return default
    has_pct = "%" in s
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if not m:
        return default
    v = float(m.group(1))
    if has_pct or v > 1.0:
        return round(v / 100.0, 4)
    return round(v, 4)


def _parse_date_str(text: str) -> str | None:
    s = text.strip()
    m = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})\b", s)
    if m:
        d, mon_s, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        mo = _MONTH_MAP.get(mon_s)
        if mo and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(20\d{2})\b", s)
    if m:
        mon_s, d, y = m.group(1).lower(), int(m.group(2)), int(m.group(3))
        mo = _MONTH_MAP.get(mon_s)
        if mo and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        mo, d = (a, b) if a <= 12 else (b, a)
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _renewal_period_from_date(iso_date: str | None) -> tuple[int, str]:
    if not iso_date:
        return 2027, "J27"
    try:
        dt = date.fromisoformat(iso_date)
    except ValueError:
        return 2027, "J27"
    yr = dt.year
    yy = yr % 100
    if dt.month <= 2:
        code = f"J{yy:02d}"
    elif dt.month <= 5:
        code = f"A{yy:02d}"
    elif dt.month <= 8:
        code = f"U{yy:02d}"
    else:
        code = f"O{yy:02d}"
    return yr, code


def _extract_region_perils(text: str) -> tuple[list[str], list[str]]:
    found: list[str] = []
    for pat, rp in _REGION_PERIL_PATTERNS:
        if pat.search(text) and rp not in found:
            found.append(rp)
    # Also catch any explicit canonical region-peril token directly
    for rp in _CANONICAL_RP_SET:
        if re.search(rf"\b{re.escape(rp)}\b", text, re.I) and rp not in found:
            found.append(rp)
    perils: list[str] = []
    for rp in found:
        p = rp.rsplit("-", 1)[-1]
        if p not in perils:
            perils.append(p)
    return found, perils


def _extract_layers_from_text(text: str) -> list[IntakeLayerDraft]:
    layers: list[IntakeLayerDraft] = []
    # Match lines containing "<limit>M xs <attachment>M" or "<limit>m excess of <attachment>m"
    line_pat = re.compile(
        r"(?:(?P<code>(?:L\d+|Layer\s*\d+))[:\s\-|]+)?"
        r"\$?(?P<limit>\d+(?:\.\d+)?)\s*(?:m|mm|mn|million)\s*"
        r"(?:xs|x/s|excess(?:\s+of)?)\s*"
        r"\$?(?P<att>\d+(?:\.\d+)?)\s*(?:m|mm|mn|million)",
        re.I,
    )
    for raw_line in text.splitlines():
        m = line_pat.search(raw_line)
        if not m:
            continue
        limit = float(m.group("limit"))
        att = float(m.group("att"))
        if limit <= 0:
            continue
        idx = len(layers) + 1
        raw_code = (m.group("code") or "").strip()
        code = re.sub(r"(?i)^layer\s*", "L", raw_code) if raw_code else f"L{idx}"
        code = code.upper()

        prem_m = re.search(r"(?:prem(?:ium)?|deposit)\s*[:=@]?\s*\$?(\d+(?:\.\d+)?)\s*(?:m|mm|mn|million)?", raw_line, re.I)
        rol_m = re.search(r"(?:rol\s*[:=@]?\s*(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*rol)", raw_line, re.I)
        line_m = re.search(r"(?:signed\s*line|written\s*line|line|share)\s*[:=@]?\s*(\d+(?:\.\d+)?)\s*%", raw_line, re.I)
        agg_m = re.search(r"\b(?:agg|aggregate)\b", raw_line, re.I)

        rol: float | None = None
        if rol_m:
            raw_rol = float(rol_m.group(1) or rol_m.group(2))
            rol = round(raw_rol / 100.0, 4) if raw_rol > 1.0 else round(raw_rol, 4)
        prem = float(prem_m.group(1)) if prem_m else 0.0
        if prem == 0.0 and rol is not None:
            prem = round(limit * rol, 4)
        elif rol is None and prem > 0.0 and limit > 0:
            rol = round(prem / limit, 4)
        share = round(min(1.0, float(line_m.group(1)) / 100.0), 4) if line_m else 1.0

        layers.append(
            IntakeLayerDraft(
                layer_code=code,
                name=f"Layer {idx} ({limit:g}M xs {att:g}M)",
                layer_type="AGG_XL" if agg_m else "OCC_XL",
                limit=round(limit, 4),
                attachment=round(att, 4),
                deposit_premium=round(prem, 4),
                rol=rol,
                written_line=share,
                signed_line=share,
                confidence=0.88 if (prem > 0 or rol is not None) else 0.72,
            )
        )
    return layers


def _extract_loss_history_from_text(text: str, default_currency: str = "USD") -> list[IntakeLossHistoryRow]:
    rows: list[IntakeLossHistoryRow] = []
    # Pattern: "2022 Hurricane Ian: $85M" or "2017 - Hurricane Irma - $62.5M"
    pat = re.compile(
        r"\b(?P<year>19\d{2}|20\d{2})\b[\s\-:|]+"
        r"(?P<event>[A-Za-z][A-Za-z0-9\s/()&'.-]{2,60}?)[\s\-:|]+"
        r"\$?(?P<loss>\d+(?:\.\d+)?)\s*(?:m|mm|mn|million)\b",
        re.I,
    )
    seen: set[tuple[int, str]] = set()
    for line in text.splitlines():
        if re.search(r"\b(?:xs|x/s|excess|inception|expiry|limit|attachment)\b", line, re.I):
            continue
        m = pat.search(line)
        if not m:
            continue
        yr = int(m.group("year"))
        ev = m.group("event").strip(" -:|")
        loss = float(m.group("loss"))
        key = (yr, ev.lower())
        if key in seen or loss <= 0:
            continue
        seen.add(key)
        rps, _ = _extract_region_perils(line)
        rows.append(
            IntakeLossHistoryRow(
                year=yr,
                occurrence_date=f"{yr}-09-01",
                event_name=ev,
                ground_up_loss_m=round(loss, 4),
                currency=default_currency,
                region_peril_key=rps[0] if rps else None,
            )
        )
    return rows


def _extract_from_text_blob(
    text: str,
    *,
    filename: str,
    hint_subject: str = "",
    preset_kv: dict[str, str] | None = None,
    preset_layers: list[IntakeLayerDraft] | None = None,
    preset_losses: list[IntakeLossHistoryRow] | None = None,
) -> tuple[IntakeExtractedData, float]:
    kv = {k.lower(): v for k, v in (preset_kv or {}).items()}
    combined = f"{hint_subject}\n{text}" if hint_subject else text
    field_conf: dict[str, float] = {}
    warnings: list[str] = []

    # Cedent
    cedent = kv.get("cedent") or kv.get("reinsured") or kv.get("insured") or ""
    if not cedent:
        m = re.search(r"(?:cedent|reinsured|insured|company)\s*[:\-]\s*([^\n;|]+)", combined, re.I)
        if m:
            cedent = m.group(1).strip()
    if cedent:
        field_conf["cedentName"] = 0.92
    else:
        stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
        cedent = stem.split()[0].title() + " Reinsured" if stem else "Unnamed Cedent (Draft)"
        field_conf["cedentName"] = 0.35
        warnings.append("Cedent name was not explicitly labelled; please verify before confirming.")

    # Programme Name
    prog_name = kv.get("programme") or kv.get("program") or kv.get("treaty") or kv.get("programme name") or ""
    if not prog_name:
        m = re.search(r"(?:programme|program|treaty|placement|slip)\s*(?:name)?\s*[:\-]\s*([^\n;|]+)", combined, re.I)
        if m:
            prog_name = m.group(1).strip()
    if prog_name:
        field_conf["programmeName"] = 0.90
    else:
        clean_subj = re.sub(r"^(?:re|fwd|fw)\s*:\s*", "", hint_subject.strip(), flags=re.I)
        prog_name = clean_subj or f"{Path(filename).stem.replace('_', ' ').replace('-', ' ').strip()} Draft"
        field_conf["programmeName"] = 0.48

    # Broker
    broker = kv.get("broker") or kv.get("intermediary") or ""
    if not broker:
        m = re.search(r"(?:broker|intermediary)\s*[:\-]\s*([^\n;|]+)", combined, re.I)
        if m:
            broker = m.group(1).strip()
    broker = broker or "Direct"

    # Currency
    currency = (kv.get("currency") or "").strip().upper()
    if currency not in {"USD", "EUR", "GBP", "JPY", "CAD", "AUD"}:
        m = re.search(r"(?:currency|ccy)\s*[:\-]\s*(USD|EUR|GBP|JPY|CAD|AUD)\b", combined, re.I)
        currency = m.group(1).upper() if m else "USD"

    # Inception & Expiry
    inc_raw = kv.get("inception") or kv.get("inception date") or kv.get("effective") or kv.get("effective date") or ""
    inception_date = _parse_date_str(inc_raw) if inc_raw else None
    if not inception_date:
        m = re.search(r"(?:inception|effective|commencement|period)\s*(?:date)?\s*[:\-]?\s*([^\n;]{4,40})", combined, re.I)
        if m:
            inception_date = _parse_date_str(m.group(1))
    exp_raw = kv.get("expiry") or kv.get("expiry date") or kv.get("expiration") or ""
    expiry_date = _parse_date_str(exp_raw) if exp_raw else None
    if not expiry_date:
        m = re.search(r"(?:expiry|expiration|to)\s*(?:date)?\s*[:\-]?\s*([^\n;]{4,40})", combined, re.I)
        if m:
            expiry_date = _parse_date_str(m.group(1))

    if inception_date:
        treaty_year, renewal_period = _renewal_period_from_date(inception_date)
        field_conf["inceptionDate"] = 0.90
        if not expiry_date:
            try:
                dt = date.fromisoformat(inception_date)
                expiry_date = f"{dt.year}-12-31" if dt.month == 1 and dt.day == 1 else f"{dt.year + 1}-{dt.month:02d}-{dt.day:02d}"
            except ValueError:
                expiry_date = f"{treaty_year}-12-31"
    else:
        treaty_year, renewal_period = 2027, "J27"
        inception_date = "2027-01-01"
        expiry_date = "2027-12-31"
        field_conf["inceptionDate"] = 0.40
        warnings.append("Inception date not detected; defaulted to 2027-01-01.")

    # Region & Peril
    rps, perils = _extract_region_perils(combined)
    if rps:
        field_conf["regionPerils"] = 0.88
    else:
        rps = ["US-FL-WS"]
        perils = ["WS"]
        field_conf["regionPerils"] = 0.35
        warnings.append("Region/peril not explicitly detected; defaulted to US-FL-WS.")

    # Layers
    layers = list(preset_layers or [])
    if not layers:
        layers = _extract_layers_from_text(combined)
    if layers:
        field_conf["layers"] = round(sum(l.confidence for l in layers) / len(layers), 2)
    else:
        field_conf["layers"] = 0.30
        warnings.append("No layer schedule detected; review and add layers before confirming.")

    # Loss history
    loss_history = list(preset_losses or [])
    if not loss_history:
        loss_history = _extract_loss_history_from_text(combined, default_currency=currency)
    field_conf["lossHistory"] = 0.85 if loss_history else 0.50

    weights = {
        "cedentName": 0.25,
        "programmeName": 0.15,
        "inceptionDate": 0.15,
        "regionPerils": 0.15,
        "layers": 0.25,
        "lossHistory": 0.05,
    }
    overall = round(sum(field_conf.get(k, 0.5) * w for k, w in weights.items()), 2)

    extracted = IntakeExtractedData(
        cedent_name=cedent,
        programme_name=prog_name,
        broker=broker,
        inception_date=inception_date,
        expiry_date=expiry_date,
        treaty_year=treaty_year,
        renewal_period=renewal_period,
        currency=currency,
        region_perils=rps,
        perils=perils,
        layers=layers,
        loss_history=loss_history,
        field_confidence=field_conf,
        warnings=warnings,
        raw_excerpt=text[:400].strip(),
    )
    return extracted, overall


def _parse_pdf_bytes(raw_bytes: bytes, *, filename: str, hint_subject: str = "") -> ParsedSubmissionFile:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw_bytes))
    chunks: list[str] = []
    total_chars = 0
    for page in reader.pages[:MAX_PDF_PAGES]:
        txt = page.extract_text() or ""
        if total_chars + len(txt) > MAX_TEXT_CHARS:
            chunks.append(txt[: MAX_TEXT_CHARS - total_chars])
            break
        chunks.append(txt)
        total_chars += len(txt)
    full_text = "\n".join(chunks)
    extracted, confidence = _extract_from_text_blob(full_text, filename=filename, hint_subject=hint_subject)
    return ParsedSubmissionFile(
        file_kind=IntakeFileKind.PDF,
        extracted=extracted,
        overall_confidence=confidence,
    )


def _parse_excel_bytes(raw_bytes: bytes, *, filename: str, hint_subject: str = "") -> ParsedSubmissionFile:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    kv: dict[str, str] = {}
    layers: list[IntakeLayerDraft] = []
    losses: list[IntakeLossHistoryRow] = []
    text_lines: list[str] = []
    total_chars = 0

    try:
        for sheet_name in wb.sheetnames[:MAX_EXCEL_SHEETS]:
            ws = wb[sheet_name]
            layer_headers: dict[str, int] | None = None
            loss_headers: dict[str, int] | None = None

            for row_idx, row in enumerate(ws.iter_rows(max_row=MAX_EXCEL_ROWS_PER_SHEET, max_col=MAX_EXCEL_COLS, values_only=True)):
                if row is None:
                    continue
                vals = [v for v in row]
                non_empty = [v for v in vals if v is not None and str(v).strip() != ""]
                if not non_empty:
                    layer_headers = None
                    loss_headers = None
                    continue

                # Accumulate bounded text excerpt for general regex extraction (region/peril, broker, etc.)
                if total_chars < MAX_TEXT_CHARS:
                    line_s = " | ".join(str(v).strip() for v in non_empty[:10])
                    text_lines.append(line_s)
                    total_chars += len(line_s) + 1

                # Detect 2-column key-value metadata rows
                if len(non_empty) == 2 and layer_headers is None and loss_headers is None:
                    k_raw = str(non_empty[0]).strip().rstrip(":").lower()
                    v_raw = str(non_empty[1]).strip()
                    if k_raw in {
                        "cedent", "reinsured", "insured", "programme", "program", "programme name",
                        "treaty", "broker", "inception", "inception date", "effective", "effective date",
                        "expiry", "expiry date", "currency", "region", "peril", "region/peril",
                    }:
                        kv[k_raw] = v_raw
                        continue

                norm_cells = [_norm_col(str(v or "")) for v in vals]

                # Check if this row is a Layer Schedule header
                has_limit = any(c in {"limit", "limit_m", "layer_limit"} for c in norm_cells)
                has_att = any(c in {"attachment", "attachment_m", "retention", "excess_of"} for c in norm_cells)
                if has_limit and has_att:
                    layer_headers = {c: i for i, c in enumerate(norm_cells) if c}
                    loss_headers = None
                    continue

                # Check if this row is a Loss History header
                has_ev = any(c in {"event", "event_name", "catastrophe", "cat_event", "description"} for c in norm_cells)
                has_loss = any(c in {"loss", "ground_up_loss", "ground_up_loss_m", "loss_m", "incurred", "incurred_loss"} for c in norm_cells)
                if has_ev and has_loss:
                    loss_headers = {c: i for i, c in enumerate(norm_cells) if c}
                    layer_headers = None
                    continue

                # Parse layer row
                if layer_headers is not None:
                    lim_idx = next((layer_headers[k] for k in ("limit", "limit_m", "layer_limit") if k in layer_headers), None)
                    att_idx = next((layer_headers[k] for k in ("attachment", "attachment_m", "retention", "excess_of") if k in layer_headers), None)
                    lim = _to_millions(vals[lim_idx]) if lim_idx is not None and lim_idx < len(vals) else None
                    att = _to_millions(vals[att_idx]) if att_idx is not None and att_idx < len(vals) else None
                    if lim is not None and lim > 0 and att is not None and att >= 0:
                        idx = len(layers) + 1
                        code_idx = next((layer_headers[k] for k in ("layer", "layer_code", "code", "id") if k in layer_headers), None)
                        name_idx = next((layer_headers[k] for k in ("name", "layer_name", "description") if k in layer_headers), None)
                        prem_idx = next((layer_headers[k] for k in ("premium", "deposit_premium", "premium_m", "deposit") if k in layer_headers), None)
                        rol_idx = next((layer_headers[k] for k in ("rol", "rol_pct", "rate_on_line") if k in layer_headers), None)
                        line_idx = next((layer_headers[k] for k in ("line", "signed_line", "written_line", "share", "line_pct") if k in layer_headers), None)
                        type_idx = next((layer_headers[k] for k in ("type", "layer_type", "structure") if k in layer_headers), None)

                        raw_code = str(vals[code_idx]).strip() if code_idx is not None and code_idx < len(vals) and vals[code_idx] else f"L{idx}"
                        code = re.sub(r"(?i)^layer\s*", "L", raw_code).upper()
                        name = str(vals[name_idx]).strip() if name_idx is not None and name_idx < len(vals) and vals[name_idx] else f"Layer {idx} ({lim:g}M xs {att:g}M)"
                        prem = _to_millions(vals[prem_idx]) if prem_idx is not None and prem_idx < len(vals) else None
                        rol = _to_fraction(vals[rol_idx], None) if rol_idx is not None and rol_idx < len(vals) else None
                        share = _to_fraction(vals[line_idx], 1.0) if line_idx is not None and line_idx < len(vals) else 1.0
                        share = min(1.0, max(0.0, share or 1.0))
                        if (prem is None or prem == 0.0) and rol is not None:
                            prem = round(lim * rol, 4)
                        elif rol is None and prem and lim > 0:
                            rol = round(prem / lim, 4)
                        raw_type = str(vals[type_idx] or "").upper() if type_idx is not None and type_idx < len(vals) else ""
                        ltype: Any = "AGG_XL" if "AGG" in raw_type else "OCC_XL"

                        layers.append(
                            IntakeLayerDraft(
                                layer_code=code,
                                name=name,
                                layer_type=ltype,
                                limit=lim,
                                attachment=att,
                                deposit_premium=prem or 0.0,
                                rol=rol,
                                written_line=share,
                                signed_line=share,
                                confidence=0.93,
                            )
                        )
                    continue

                # Parse loss history row
                if loss_headers is not None:
                    ev_idx = next((loss_headers[k] for k in ("event", "event_name", "catastrophe", "cat_event", "description") if k in loss_headers), None)
                    loss_idx = next((loss_headers[k] for k in ("loss", "ground_up_loss", "ground_up_loss_m", "loss_m", "incurred", "incurred_loss") if k in loss_headers), None)
                    yr_idx = next((loss_headers[k] for k in ("year", "loss_year", "uw_year", "date") if k in loss_headers), None)
                    rp_idx = next((loss_headers[k] for k in ("region_peril", "region_peril_key", "peril", "region") if k in loss_headers), None)
                    ev_val = str(vals[ev_idx]).strip() if ev_idx is not None and ev_idx < len(vals) and vals[ev_idx] else ""
                    loss_val = _to_millions(vals[loss_idx]) if loss_idx is not None and loss_idx < len(vals) else None
                    if ev_val and loss_val is not None and loss_val > 0:
                        yr: int | None = None
                        occ_dt: str | None = None
                        if yr_idx is not None and yr_idx < len(vals) and vals[yr_idx] is not None:
                            raw_y = str(vals[yr_idx]).strip()
                            ym = re.search(r"(19\d{2}|20\d{2})", raw_y)
                            if ym:
                                yr = int(ym.group(1))
                            occ_dt = _parse_date_str(raw_y) or (f"{yr}-09-01" if yr else None)
                        rp_val = str(vals[rp_idx]).strip() if rp_idx is not None and rp_idx < len(vals) and vals[rp_idx] else None
                        losses.append(
                            IntakeLossHistoryRow(
                                year=yr,
                                occurrence_date=occ_dt,
                                event_name=ev_val,
                                ground_up_loss_m=loss_val,
                                currency=(kv.get("currency") or "USD").upper(),
                                region_peril_key=rp_val,
                            )
                        )
    finally:
        wb.close()

    full_text = "\n".join(text_lines)
    extracted, confidence = _extract_from_text_blob(
        full_text,
        filename=filename,
        hint_subject=hint_subject,
        preset_kv=kv,
        preset_layers=layers,
        preset_losses=losses,
    )
    return ParsedSubmissionFile(
        file_kind=IntakeFileKind.EXCEL,
        extracted=extracted,
        overall_confidence=confidence,
    )


def parse_submission_bytes(
    raw_bytes: bytes,
    *,
    filename: str,
    hint_subject: str = "",
) -> ParsedSubmissionFile:
    """Classify and parse an uploaded submission file in bounded memory.

    YELT and ELT modelling files are detected deterministically and never sent to any LLM.
    """
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Submission file '{filename}' exceeds the {MAX_UPLOAD_MB} MB intake limit.")

    ext = Path(filename).suffix.lower()

    if ext == ".parquet":
        spec = _detect_parquet_yelt(raw_bytes)
        return ParsedSubmissionFile(
            file_kind=IntakeFileKind.YELT,
            yelt_spec=spec,
            overall_confidence=0.98,
        )

    if ext == ".csv":
        kind, yelt_spec, elt_spec = _detect_csv_modelling(raw_bytes)
        if kind == IntakeFileKind.YELT and yelt_spec is not None:
            return ParsedSubmissionFile(
                file_kind=IntakeFileKind.YELT,
                yelt_spec=yelt_spec,
                overall_confidence=0.98,
            )
        if kind == IntakeFileKind.ELT and elt_spec is not None:
            return ParsedSubmissionFile(
                file_kind=IntakeFileKind.ELT,
                elt_spec=elt_spec,
                overall_confidence=0.96,
            )

    if ext == ".pdf":
        return _parse_pdf_bytes(raw_bytes, filename=filename, hint_subject=hint_subject)

    if ext in (".xlsx", ".xlsm"):
        return _parse_excel_bytes(raw_bytes, filename=filename, hint_subject=hint_subject)

    # Plain text / fallback CSV / slip text
    text = raw_bytes[:MAX_TEXT_CHARS].decode("utf-8-sig", errors="replace")
    extracted, confidence = _extract_from_text_blob(text, filename=filename, hint_subject=hint_subject)
    return ParsedSubmissionFile(
        file_kind=IntakeFileKind.TEXT,
        extracted=extracted,
        overall_confidence=confidence,
    )
