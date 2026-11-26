"""Region and peril of each uploaded loss file, carried onto the loss source.

A cat-model loss file (RMS, Verisk, KCC, or a direct ELT/YELT upload) is one region
and one peril when the upload says so. A file that does not declare them contributes
one entry for each region-peril key actually present in the file. The zone/peril
filter may only offer what those files contain.
"""

from __future__ import annotations

import json
from typing import Any

from app.models.domain import GraphSpec
from app.models.enums import NodeType


def split_region_peril(key: str) -> tuple[str, str] | None:
    text = str(key or "").strip()
    if not text or text == "ALL" or "-" not in text:
        return None
    region, peril = text.rsplit("-", 1)
    if not region or not peril:
        return None
    return region, peril


def loss_file(
    *,
    sim_set_id: str,
    name: str,
    region: str,
    peril: str,
    vendor: str | None = None,
) -> dict[str, Any]:
    region_peril = f"{region}-{peril}"
    return {
        "fileId": f"{sim_set_id}:{region_peril}",
        "simSetId": sim_set_id,
        "name": name,
        "region": region,
        "peril": peril,
        "regionPeril": region_peril,
        "vendor": vendor or None,
    }


def files_from_keys(
    keys: list[str],
    *,
    sim_set_id: str,
    name: str,
    vendor: str | None = None,
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        parts = split_region_peril(str(key))
        if parts is None:
            continue
        region_peril = f"{parts[0]}-{parts[1]}"
        if region_peril in seen:
            continue
        seen.add(region_peril)
        files.append(loss_file(sim_set_id=sim_set_id, name=name, region=parts[0], peril=parts[1], vendor=vendor))
    return files


def files_from_stored(
    loss_files_json: str | None,
    region_perils: list[str],
    *,
    sim_set_id: str,
    name: str,
) -> list[dict[str, Any]]:
    """Prefer the stored per-file records. Older sim sets only have a flat key list."""
    try:
        stored = json.loads(loss_files_json or "[]")
    except json.JSONDecodeError:
        stored = []
    if isinstance(stored, list) and any(isinstance(item, dict) and item.get("region") and item.get("peril") for item in stored):
        return [item for item in stored if isinstance(item, dict)]
    return files_from_keys(region_perils, sim_set_id=sim_set_id, name=name)


def _explicit_files(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("region") and item.get("peril")]


def files_for_loss_source(
    params: dict[str, Any],
    sim_files: dict[str, list[dict[str, Any]]],
    programme_sim_set_id: str,
) -> list[dict[str, Any]]:
    """Files attached to a loss source. An explicit ``lossFiles`` list wins.

    Otherwise use the sim set named on the node, then the programme sim set.
    That last step covers older graphs whose loss source only knew a sim set id,
    and graphs whose source node was left blank while the programme still has one.
    """
    explicit = _explicit_files(params.get("lossFiles"))
    if explicit:
        return explicit
    sim_set_id = str(params.get("simSetId") or "").strip() or programme_sim_set_id
    return list(sim_files.get(sim_set_id) or [])


def _upstream_sources(graph: GraphSpec, start_id: str) -> list[Any]:
    incoming: dict[str, list[str]] = {}
    for edge in graph.edges:
        incoming.setdefault(edge.target, []).append(edge.source)
    by_id = {node.node_id: node for node in graph.nodes}
    seen: set[str] = set()
    stack = list(incoming.get(start_id, []))
    sources = []
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        node = by_id.get(node_id)
        if node is None:
            continue
        if node.node_type == NodeType.LOSS_SOURCE:
            sources.append(node)
            continue
        stack.extend(incoming.get(node_id, []))
    return sources


def choices_from_files(files: list[dict[str, Any]]) -> tuple[set[str], set[str], set[str]]:
    regions: set[str] = set()
    perils: set[str] = set()
    pairs: set[str] = set()
    for item in files:
        region = str(item.get("region") or "").strip()
        peril = str(item.get("peril") or "").strip()
        if region:
            regions.add(region)
        if peril:
            perils.add(peril)
        if region and peril:
            pairs.add(str(item.get("regionPeril") or f"{region}-{peril}"))
    return regions, perils, pairs


def validate_filters_match_loss_files(
    graph: GraphSpec,
    sim_files: dict[str, list[dict[str, Any]]],
    programme_sim_set_id: str,
) -> None:
    """Reject a zone/peril selection that no upstream loss file actually contains."""
    for node in graph.nodes:
        if node.node_type != NodeType.REGION_PERIL_FILTER:
            continue
        sources = _upstream_sources(graph, node.node_id)
        files: list[dict[str, Any]] = []
        for source in sources:
            files.extend(files_for_loss_source(source.params, sim_files, programme_sim_set_id))
        regions, perils, pairs = choices_from_files(files)
        zones = [str(z) for z in (node.params.get("zones") or []) if z and z != "ALL"]
        peril_sel = [str(p) for p in (node.params.get("perils") or []) if p and p != "ALL"]
        explicit = [str(k) for k in (node.params.get("regionPerils") or []) if k and k != "ALL"]
        missing = [z for z in zones if z not in regions] + [p for p in peril_sel if p not in perils] + [
            k for k in explicit if k not in pairs
        ]
        if not missing:
            continue
        available = ", ".join(sorted(pairs)) or "none"
        raise ValueError(
            f"Zone/peril filter '{node.label}' can only use regions and perils from the loss files "
            f"on the upstream loss source. Not in those files: {', '.join(missing)}. "
            f"Available: {available}."
        )
