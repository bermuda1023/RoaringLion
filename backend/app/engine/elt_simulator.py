"""ELT-to-YELT Monte Carlo Simulator.

Converts an Event Loss Table (event_id, annual_rate, mean_loss, sd_indep, sd_corr, max_loss)
into a canonical trial-sorted YELT using seeded Poisson frequency + LogNormal secondary uncertainty
matching each event's exact mean and total standard deviation (capped at max_loss when provided).
"""

from __future__ import annotations

import numpy as np

from app.engine.sim_generator import REGION_PERILS, RP_TO_ID, YeltTable
from app.models.phase1 import EltRowInput
from app.services.lineage import canonical_hash


def simulate_elt_to_yelt(
    *,
    sim_set_id: str,
    name: str,
    event_catalog_id: str,
    rows: list[EltRowInput],
    num_trials: int = 50_000,
    currency: str = "USD",
    seed: int = 42,
) -> tuple[YeltTable, float]:
    """Simulate an ELT into a canonical YeltTable.

    Returns (yelt_table, theoretical_elt_aal).
    """
    max_rate = 1_000.0
    max_expected_events = 2_000_000
    total_rate = 0.0
    for row in rows:
        if row.annual_rate <= 0.0 or row.mean_loss <= 0.0:
            continue
        if not np.isfinite(row.annual_rate) or row.annual_rate > max_rate:
            raise ValueError(
                f"ELT annual_rate ({row.annual_rate}) exceeds maximum allowed rate ({max_rate:g} events/year)."
            )
        total_rate += float(row.annual_rate)
    if total_rate * num_trials > max_expected_events:
        raise ValueError(
            f"ELT expected simulated events ({total_rate * num_trials:,.0f}) exceeds maximum allowed ({max_expected_events:,})."
        )

    rng = np.random.default_rng(seed)
    t_parts: list[np.ndarray] = []
    e_parts: list[np.ndarray] = []
    d_parts: list[np.ndarray] = []
    r_parts: list[np.ndarray] = []
    l_parts: list[np.ndarray] = []

    theoretical_aal = 0.0
    for row in rows:
        if row.annual_rate <= 0.0 or row.mean_loss <= 0.0:
            continue
        theoretical_aal += row.annual_rate * row.mean_loss
        counts = rng.poisson(row.annual_rate, size=num_trials)
        total = int(counts.sum())
        if total == 0:
            continue

        t_arr = np.repeat(np.arange(1, num_trials + 1, dtype=np.uint32), counts)
        total_sd = float(np.hypot(row.sd_indep, row.sd_corr))
        if total_sd > 1e-12:
            cv2 = (total_sd / row.mean_loss) ** 2
            sigma2 = float(np.log(1.0 + cv2))
            sigma = float(np.sqrt(sigma2))
            mu = float(np.log(row.mean_loss) - 0.5 * sigma2)
            sev = rng.lognormal(mean=mu, sigma=sigma, size=total)
        else:
            sev = np.full(total, row.mean_loss, dtype=np.float64)

        if row.max_loss is not None and row.max_loss > 0.0:
            sev = np.minimum(sev, row.max_loss)

        rp_id = RP_TO_ID.get(row.region_peril_key, 0)
        day = int(max(1, min(365, row.day_of_year)))

        t_parts.append(t_arr)
        e_parts.append(np.full(total, row.event_id, dtype=np.uint64))
        d_parts.append(np.full(total, day, dtype=np.uint16))
        r_parts.append(np.full(total, rp_id, dtype=np.int32))
        l_parts.append(sev)

    if t_parts:
        trial_ids = np.concatenate(t_parts)
        event_ids = np.concatenate(e_parts)
        day_of_year = np.concatenate(d_parts)
        rp_ids = np.concatenate(r_parts)
        losses = np.concatenate(l_parts)
        order = np.lexsort((event_ids, day_of_year, trial_ids))
        trial_ids = np.ascontiguousarray(trial_ids[order], dtype=np.uint32)
        event_ids = np.ascontiguousarray(event_ids[order], dtype=np.uint64)
        day_of_year = np.ascontiguousarray(day_of_year[order], dtype=np.uint16)
        rp_ids = np.ascontiguousarray(rp_ids[order], dtype=np.int32)
        losses = np.ascontiguousarray(losses[order], dtype=np.float64)
    else:
        trial_ids = np.zeros(0, dtype=np.uint32)
        event_ids = np.zeros(0, dtype=np.uint64)
        day_of_year = np.zeros(0, dtype=np.uint16)
        rp_ids = np.zeros(0, dtype=np.int32)
        losses = np.zeros(0, dtype=np.float64)

    chash = canonical_hash({
        "sim_set_id": sim_set_id,
        "num_trials": num_trials,
        "seed": seed,
        "total_events": int(len(losses)),
        "sum_loss": round(float(losses.sum()), 4),
    })

    table = YeltTable(
        sim_set_id=sim_set_id,
        name=name,
        event_catalog_id=event_catalog_id,
        num_trials=num_trials,
        currency=currency,
        trial_ids=trial_ids,
        event_ids=event_ids,
        day_of_year=day_of_year,
        rp_ids=rp_ids,
        losses=losses,
        content_hash=chash,
    )
    return table, theoretical_aal
