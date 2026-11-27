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
  - `PRICING_OUTPUT` params `contractId` / `layerNodeId` link a CMS contract layer; its premium, written/signed lines and brokerage are applied to the node's input loss stream (restated to 100% of the upstream layer's placed share). Without a link it prices the layer feeding it. Results are returned as `pricing` on the node metrics. The link is 1:1: a second Pricing Output cannot claim the same CMS layer. When a programme graph uses any explicit link (or an explicitly unlinked Pricing Output), only linked Pricing Outputs roll up into contract totals, portfolio, optimizer and retro allocation.
  - `GET /api/graph-templates` returns blank-canvas node types (`source: BLANK`, `placement: BLANK`) and, for a programme, one CMS suggestion per contracted layer (`source: CMS`). `placement` is `EXISTING_LAYER` for this programme's own layer node, or `NEW_COPY` for a prior-year layer and for a programme whose graph has no layer node yet. Each CMS layer suggestion includes exactly one `pricingOutput` (`nodeType: PRICING_OUTPUT`, `offset` `{x: 320, y: 0}`). `params.contractId` is the programme that owns the contracted layer. `params.layerNodeId` is that layer's node id when the layer already exists; a programme with no layer node yet omits it, and placing the suggestion writes the new layer id onto the Pricing Output so the pair is still 1:1.
  - Dropping a CMS layer suggestion creates the layer only when that node is not already on the canvas, and ensures exactly one Pricing Output. New nodes sit on the layer column (x of the existing layers, otherwise 600) with the Pricing Output on the same row. They are outside the blank-canvas drop zone (x 420–560 and y 80–260). If the 320 offset would fall inside that zone, the Pricing Output moves to x 600 on the same row. A layer already on the canvas is left where it is. A layer that already feeds a Pricing Output keeps that node and is not given a second one.
  - `YELT_OUTPUT` param `view` (`ADJUSTED` | `UNADJUSTED`). `GET /api/programmes/{id}/nodes/{nodeId}/yelt` summarises the node's event losses, `/yelt/export?format=csv|parquet` downloads them, and `POST /yelt/save` registers them as a `DERIVED` simulation set.

Edge semantics:
- `sign`: `+1` (solid additive) or `-1` (dashed subtractive / inuring / deductible offset)
- `weight`: `float64` (default `1.0`)

## 5. Programme & Contract Status (`ProgrammeStatus`)

Aligned with ExposureEclipse (`BOUND` + dates in force = active portfolio):
- `SUBMISSION`, `INDICATIVE`, `QUOTED`, `AUTHORIZED`, `FIRM_ORDER`, `BOUND`, `BOUND_FOR_OPS`, `DECLINED`, `NTU`, `EXPIRED`, `PLANNED`

The Portfolio Optimizer chooses its deals by status:
- `includedStatuses` lists the statuses whose deals form the portfolio. Any status is allowed, including `DECLINED`, `NTU` and `EXPIRED`. It is a body field on `POST /api/optimizer/solve`, `/frontier` and `/explore`, and a repeated query parameter on `GET /api/optimizer/candidates` and `/layers`.
- When it is omitted, `mode: CURRENT` uses `QUOTED`, `FIRM_ORDER`, `BOUND`, and `mode: FORWARD_PLANNING` uses every status except `DECLINED`, `NTU`, `EXPIRED`. An empty list is rejected with 422, as is a selection that matches no deal. ILW-structure contracts never enter, whatever their status.
- Every optimizer row (`deals[]`, `layers[]`, `initialLayers[]`) carries the deal's `status`. The candidate retro ILW hedge row is not a deal; it has `direction: EXTERNAL_RETRO` and `status: PLANNED`. The `/solve` and `/explore` responses echo the `includedStatuses` they were built from, and their manifest `parameters` record `included_statuses` (as requested, `null` for the defaults) and `effective_statuses`. Selecting a status never changes a deal's status.
- A deal enters at its recorded signed line whatever its status. A layer recorded at a 0% line (usual for a declined or NTU deal) has `currentShare` 0 and is evaluated at 100% so that its 100%-share loss, premium and limit are real figures the optimizer can scale.
- A layer priced through a Pricing Output link rolls up under the linked contract's status, and only when the programme whose graph prices it is itself in the portfolio. With `includedStatuses` set, the linked contract's status must be selected too. Without it, the link resolves as it always has, against every contract that is not `DECLINED`, `NTU` or `EXPIRED`, so sending a mode's default statuses explicitly is not the same request as omitting the field.
- `GET /api/optimizer/candidates` lists the deals a portfolio would be built from, one page of programmes at a time (`limit` up to 100, `offset`, ordered by cedent, programme, id). `search` matches cedent, programme, broker or programme id, ignoring case. It returns:
  - `includedStatuses` (the statuses in effect), `defaultStatuses` (the mode's defaults) and `usesDefaultStatuses` (true when the request named none);
  - `statusCounts[]`: `status`, `programmes`, `included`, `defaultIncluded` for every status;
  - `universeProgrammes` (programmes in the selected statuses) and `matchedProgrammes` (those also matching `search`);
  - `programmeIds` for the page and `layers[]`, the layers those programmes' graphs roll up, each with its deal `status`. A layer that another programme's graph prices is listed with that programme;
  - `retroCandidate`, the candidate retro ILW hedge, separate from the deals.

## 6. Deterministic Loss Source Types (`LossSourceType`)

- `MANUAL`, `BORDEREAUX_UPLOAD`, `EXPOSURE_ECLIPSE`, `HISTORICAL_EVENT`, `PROGRAMME_HISTORY`, `CLAIM`, `STOCHASTIC_PICK`, `SCENARIO_INVENTED`, `SCENARIO_PERTURBED`

## 7. Lineage Link Types (`LinkType`)

- `DERIVED_FROM`, `USES_STRUCTURE`, `USES_LOSSES`, `USES_UPLIFT`, `BRANCHED_FROM`, `BOUND_AS`, `SUPERSEDES`, `HEDGED_BY`, `EXPORTED_FROM`

## 8. Currency & Traceability Rules

1. Never silently mix currencies. Every monetary field carries `currency`.
2. Every calculated response carries `manifestId` and `breadcrumb[]` linking back to input entity IDs, versions, content hashes, and assumptions.
3. Deterministic and stochastic runs call the exact same financial-terms code in `engine/` (verified by parity tests).
