# CONTRACTS.md — Canonical Enums, Schemas & Rules (Single Source of Truth)

Aligned with `ExposureEclipse/docs/CONTRACTS.md`. Every wire enum is `UPPER_SNAKE_CASE`, JSON keys are `camelCase`, currencies are explicit ISO 4217 codes (never silently mixed), and every computed figure carries a breadcrumb manifest ID.

## 1. Views of Risk (`ViewOfRisk`)

Two views only per deal:
- `UNADJUSTED` — simulation set exactly as supplied.
- `ADJUSTED` — `UNADJUSTED` with that deal's own versioned uplift set applied.

## 2. Granular Region-Peril & Peril Codes (`PerilCode`)

Canonical peril codes (superset of ExposureEclipse):
- `WS` — Windstorm / Hurricane / Typhoon
- `EQ` — Earthquake
- `CS` — Convective Storm (SCS)
- `FL` — Flood
- `WF` — Wildfire
- `FR` — Fire
- `WT` — Winterstorm
- `TR` — Terrorism
- `CY` — Cyber
- `ALL` — Filter wildcard only

Granular region-peril keys follow `<COUNTRY>-<SUB_REGION>-<PERIL>` (e.g. `US-FL-WS`, `US-FL-TRI-WS`, `US-TX-WS`, `US-SE-WS`, `US-CA-EQ`, `JP-KANTO-EQ`, `EU-DE-WS`).

## 3. Canonical YELT Schema

Columnar sorted by `(trial_id, day_of_year, event_id)`:
- `trial_id`: `uint32` (1..N simulation years)
- `event_id`: `uint64` (event identifier within `event_catalog_id`)
- `day_of_year`: `uint16` (1..365)
- `region_peril_key`: `string` (dictionary/integer-encoded in kernel)
- `loss_amount`: `float64`
- `currency`: ISO 4217 (`USD`, `EUR`, `GBP`, `JPY`, ...)
- `event_catalog_id`: `string` (single catalogue assumed in Phase 0, e.g. `SYNTH_CAT_V1`)

## 4. Layer & DAG Node Types (`NodeType`)

- Sources: `LOSS_SOURCE`, `PORTFOLIO_ROLLUP`, `INDUSTRY_INDEX_SOURCE`
- Transforms & Filters: `REGION_PERIL_FILTER`, `DATE_WINDOW_FILTER`, `LOSS_SIZE_FILTER`, `EVENT_ORDER_FILTER`, `COMBINATION`, `UPLIFT`, `FX_CONVERSION`
- Financial Terms: `OCC_XL`, `AGG_XL`, `QUOTA_SHARE`, `AGG_QUOTA_SHARE`, `SURPLUS_SHARE`, `STOP_LOSS`, `ILW_OPTION`, `GENERIC`
- Outputs: `PRICING_OUTPUT`, `YELT_OUTPUT`, `EP_OUTPUT`
  - `PRICING_OUTPUT` params `contractId` / `layerNodeId` link a CMS contract layer; its premium, written/signed lines and brokerage are applied to the node's input loss stream (restated to 100% of the upstream layer's placed share). Without a link it prices the layer feeding it. Results are returned as `pricing` on the node metrics.
  - `YELT_OUTPUT` param `view` (`ADJUSTED` | `UNADJUSTED`). `GET /api/programmes/{id}/nodes/{nodeId}/yelt` summarises the node's event losses, `/yelt/export?format=csv|parquet` downloads them, and `POST /yelt/save` registers them as a `DERIVED` simulation set.

Edge semantics:
- `sign`: `+1` (solid additive) or `-1` (dashed subtractive / inuring / deductible offset)
- `weight`: `float64` (default `1.0`)

## 5. Programme & Contract Status (`ProgrammeStatus`)

Aligned with ExposureEclipse (`BOUND` + dates in force = active portfolio):
- `SUBMISSION`, `INDICATIVE`, `QUOTED`, `AUTHORIZED`, `FIRM_ORDER`, `BOUND`, `BOUND_FOR_OPS`, `DECLINED`, `NTU`, `EXPIRED`, `PLANNED`

## 6. Deterministic Loss Source Types (`LossSourceType`)

- `MANUAL`, `BORDEREAUX_UPLOAD`, `EXPOSURE_ECLIPSE`, `HISTORICAL_EVENT`, `PROGRAMME_HISTORY`, `CLAIM`, `STOCHASTIC_PICK`, `SCENARIO_INVENTED`, `SCENARIO_PERTURBED`

## 7. Lineage Link Types (`LinkType`)

- `DERIVED_FROM`, `USES_STRUCTURE`, `USES_LOSSES`, `USES_UPLIFT`, `BRANCHED_FROM`, `BOUND_AS`, `SUPERSEDES`, `HEDGED_BY`, `EXPORTED_FROM`

## 8. Currency & Traceability Rules

1. Never silently mix currencies. Every monetary field carries `currency`.
2. Every calculated response carries `manifestId` and `breadcrumb[]` linking back to input entity IDs, versions, content hashes, and assumptions.
3. Deterministic and stochastic runs call the exact same financial-terms code in `engine/` (verified by parity tests).
