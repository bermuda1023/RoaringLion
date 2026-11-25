"""Smart node labels: `<name> — <terms>`, where terms are generated from the node's parameters.

Mirrors frontend/src/utils/nodeLabels.ts; keep the two in sync.
Params used: `autoLabel` (False = fully custom label, never rewritten) and `labelName` (user-edited name part).
"""

from __future__ import annotations

import math
import re
from typing import Any

SEP = " — "


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _round2(v: float) -> float:
    # Half away from zero, identical to the frontend (Python's format() rounds half-to-even).
    r = math.floor(abs(v) * 100 + 0.5) / 100
    return -r if v < 0 else r


def _trim(v: float) -> str:
    s = f"{_round2(v):.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _amt(v: float) -> str:
    return f"{_trim(v / 1000)}bn" if abs(v) >= 1000 else f"{_trim(v)}m"


def _pct(r: float) -> str:
    return f"{_trim(r * 100)}%"


def _ordinal(n: float) -> str:
    i = int(round(n))
    if 10 <= i % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(i % 10, "th")
    return f"{i}{suf}"


def _share_suffix(p: dict) -> str:
    placed = _num(p.get("placedShare"))
    return f" · {_pct(placed)} share" if placed is not None and abs(placed - 1.0) > 1e-9 else ""


def _list(v: Any) -> list[str]:
    return [str(x) for x in v if str(x) != "ALL"] if isinstance(v, list) else []


def node_terms(node_type: str, params: dict) -> str | None:
    t = getattr(node_type, "value", node_type)
    p = params or {}
    if t == "OCC_XL":
        lim, att = _num(p.get("occLimit")), _num(p.get("occAttachment"))
        if lim is None or att is None:
            return None
        s = f"{_amt(lim)} xs {_amt(att)}"
        reinst = _num(p.get("reinstatements"))
        if reinst is not None:
            rip = _num(p.get("ripRate"))
            s += f" ({_trim(reinst)}@{_pct(1.0 if rip is None else rip)})"
        return s + _share_suffix(p)
    if t == "AGG_XL":
        lim, att = _num(p.get("aggLimit")), _num(p.get("aggAttachment"))
        if lim is None or att is None:
            return None
        s = f"{_amt(lim)} xs {_amt(att)}"
        cap = _num(p.get("perEventCap"))
        if cap is not None:
            s += f" · {_amt(cap)} event cap"
        return s + _share_suffix(p)
    if t in ("QUOTA_SHARE", "AGG_QUOTA_SHARE"):
        ces = _num(p.get("cessionShare"))
        if ces is None:
            return None
        s = f"{_pct(ces)} ceded"
        ev, agg = _num(p.get("eventCap")), _num(p.get("aggCap"))
        if ev is not None:
            s += f" · {_amt(ev)} event cap"
        if agg is not None:
            s += f" · {_amt(agg)} agg cap"
        return s + _share_suffix(p)
    if t == "SURPLUS_SHARE":
        lines, ret = _num(p.get("numLines")), _num(p.get("retentionLine"))
        if lines is None or ret is None:
            return None
        return f"{_trim(lines)} lines × {_amt(ret)}" + _share_suffix(p)
    if t == "ILW_OPTION":
        lim, trig = _num(p.get("limit")), _num(p.get("trigger"))
        if lim is None or trig is None:
            return None
        return f"{_amt(lim)} @ {_trim(trig)}bn industry" + _share_suffix(p)
    if t == "CASCADING_TOWER":
        layers = p.get("layers") or []
        lims = [_num(l.get("occLimit")) for l in layers if isinstance(l, dict)]
        atts = [_num(l.get("occAttachment")) for l in layers if isinstance(l, dict)]
        if not layers or any(x is None for x in lims) or any(x is None for x in atts):
            return None
        n = len(layers)
        return f"{n} layer{'s' if n != 1 else ''} · {_amt(sum(lims))} xs {_amt(min(atts))}"
    if t == "REGION_PERIL_FILTER":
        zones, perils, rps = _list(p.get("zones")), _list(p.get("perils")), _list(p.get("regionPerils"))
        parts = [x for x in ("/".join(zones), "/".join(perils), ", ".join(rps)) if x]
        return " · ".join(parts) if parts else "All zones & perils"
    if t == "EVENT_ORDER_FILTER":
        lo = _num(p.get("minOrder"))
        if lo is None:
            return None
        hi = _num(p.get("maxOrder"))
        s = f"{_ordinal(lo)}+ events" if hi is None else (
            f"{_ordinal(lo)} event" if int(hi) == int(lo) else f"{_ordinal(lo)}–{_ordinal(hi)} events"
        )
        thr = _num(p.get("minLossThreshold"))
        if thr:
            s += f" ≥ {_amt(thr)}"
        return s
    if t == "DATE_WINDOW_FILTER":
        a, b = _num(p.get("startDay")), _num(p.get("endDay"))
        if a is None or b is None:
            return None
        return f"Day {_trim(a)}–{_trim(b)}"
    if t == "LOSS_SIZE_FILTER":
        lo, hi = _num(p.get("minLoss")), _num(p.get("maxLoss"))
        if hi is not None:
            return f"{_amt(lo or 0)}–{_amt(hi)}"
        return f"≥ {_amt(lo)}" if lo else "All loss sizes"
    if t == "UPLIFT":
        f = _num(p.get("factor"))
        if f is None:
            return None
        s = f"×{_round2(f):.2f}"
        add = _num(p.get("additiveLoad"))
        if add:
            s += f" {'+' if add > 0 else '−'} {_amt(abs(add))}"
        return s
    return None


_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")


def name_from_label(label: str) -> str:
    label = (label or "").strip()
    if SEP in label:
        return label.split(SEP, 1)[0].strip()
    return _TRAILING_PAREN.sub("", label).strip() or label


def label_name(params: dict, label: str) -> str:
    stored = (params or {}).get("labelName")
    return str(stored).strip() if isinstance(stored, str) else name_from_label(label)


def compose_label(name: str, terms: str | None) -> str:
    name = (name or "").strip()
    if not terms:
        return name
    return f"{name}{SEP}{terms}" if name else terms


def smart_label(node_type: str, params: dict, label: str) -> str:
    """The label a node should carry given its params; custom or term-less nodes keep their label."""
    if (params or {}).get("autoLabel") is False:
        return label
    terms = node_terms(node_type, params)
    if terms is None:
        return label
    return compose_label(label_name(params, label), terms)


def relabel_graph(graph: Any) -> Any:
    for node in graph.nodes:
        node.label = smart_label(node.node_type, node.params, node.label)
    return graph
