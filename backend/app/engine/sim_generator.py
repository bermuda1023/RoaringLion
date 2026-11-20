"""Deterministic synthetic granular region-peril YELT generator.

Generates:
1. A realistic 100,000-trial (or configurable) granular region-peril YELT on a single
   shared event catalogue (`SYNTH_CAT_V1`), sorted by `(trial_id, day_of_year, event_id)`.
2. A closed-form Poisson-Pareto YELT for exact analytical golden verification tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.services.lineage import canonical_hash

REGION_PERILS: list[str] = [
    "US-FL-WS",
    "US-TX-WS",
    "US-SE-WS",
    "US-CA-EQ",
    "JP-KANTO-EQ",
    "EU-DE-WS",
]

RP_TO_ID: dict[str, int] = {k: i for i, k in enumerate(REGION_PERILS)}


@dataclass(slots=True)
class YeltTable:
    sim_set_id: str
    name: str
    event_catalog_id: str
    num_trials: int
    currency: str
    trial_ids: np.ndarray       # uint32, sorted ascending (1..=num_trials)
    event_ids: np.ndarray       # uint64
    day_of_year: np.ndarray     # uint16
    rp_ids: np.ndarray          # int32 (index into REGION_PERILS)
    losses: np.ndarray          # float64 ($M)
    content_hash: str


def generate_synthetic_sim_set(
    *,
    sim_set_id: str = "sim-synth-cat-v1-100k",
    name: str = "Synthetic Global Cat 100k (Single Catalogue)",
    num_trials: int = 100_000,
    seed: int = 20261120,
) -> YeltTable:
    """Generate a correlated, realistic granular region-peril YELT in $M."""
    rng = np.random.default_rng(seed)

    # Per region-peril: (annual_freq, pareto_alpha, scale_m, peak_day_mean, peak_day_sd)
    specs = [
        ("US-FL-WS", 0.55, 1.95, 22.0, 255, 28),
        ("US-TX-WS", 0.35, 2.05, 16.0, 245, 30),
        ("US-SE-WS", 0.45, 2.15, 14.0, 260, 28),
        ("US-CA-EQ", 0.18, 1.85, 26.0, 180, 100),
        ("JP-KANTO-EQ", 0.22, 1.90, 20.0, 180, 100),
        ("EU-DE-WS", 0.65, 2.35, 10.0, 35, 40),
    ]

    t_parts: list[np.ndarray] = []
    e_parts: list[np.ndarray] = []
    d_parts: list[np.ndarray] = []
    r_parts: list[np.ndarray] = []
    l_parts: list[np.ndarray] = []

    for rp_idx, (rp_key, lam, alpha, scale, day_mu, day_sd) in enumerate(specs):
        counts = rng.poisson(lam, size=num_trials)
        total = int(counts.sum())
        if total == 0:
            continue
        t_arr = np.repeat(np.arange(1, num_trials + 1, dtype=np.uint32), counts)
        u = rng.uniform(0.0, 0.99999, size=total)
        # Shifted Pareto: P(X > x) = (scale / (x + scale))^alpha, capped at 600M for realism
        sev = np.minimum(scale * ((1.0 - u) ** (-1.0 / alpha) - 1.0), 600.0)
        days = (np.round(rng.normal(day_mu, day_sd, size=total)).astype(np.int64) % 365 + 1).astype(np.uint16)
        ev_ids = (np.uint64(rp_idx + 1) * np.uint64(10_000_000)) + np.arange(total, dtype=np.uint64)

        t_parts.append(t_arr)
        e_parts.append(ev_ids)
        d_parts.append(days)
        r_parts.append(np.full(total, rp_idx, dtype=np.int32))
        l_parts.append(sev)

    trial_ids = np.concatenate(t_parts)
    event_ids = np.concatenate(e_parts)
    day_of_year = np.concatenate(d_parts)
    rp_ids = np.concatenate(r_parts)
    losses = np.concatenate(l_parts)

    # Chronological sort within each trial_id: lexsort uses last key as primary
    order = np.lexsort((event_ids, day_of_year, trial_ids))
    trial_ids = np.ascontiguousarray(trial_ids[order], dtype=np.uint32)
    event_ids = np.ascontiguousarray(event_ids[order], dtype=np.uint64)
    day_of_year = np.ascontiguousarray(day_of_year[order], dtype=np.uint16)
    rp_ids = np.ascontiguousarray(rp_ids[order], dtype=np.int32)
    losses = np.ascontiguousarray(losses[order], dtype=np.float64)

    chash = canonical_hash({
        "sim_set_id": sim_set_id,
        "num_trials": num_trials,
        "seed": seed,
        "total_events": int(len(losses)),
        "sum_loss": round(float(losses.sum()), 4),
    })

    return YeltTable(
        sim_set_id=sim_set_id,
        name=name,
        event_catalog_id="SYNTH_CAT_V1",
        num_trials=num_trials,
        currency="USD",
        trial_ids=trial_ids,
        event_ids=event_ids,
        day_of_year=day_of_year,
        rp_ids=rp_ids,
        losses=losses,
        content_hash=chash,
    )


def analytic_pareto_occ_xl_el(lam: float, alpha: float, xm: float, attachment: float, limit: float) -> float:
    """Exact closed-form Expected Loss for an unlimited-reinstatement OccXL on Poisson(lam) × Pareto(alpha, xm).

    For X ~ Pareto(alpha, xm) with survival S(x) = (xm / x)^alpha for x >= xm (and S(x) = 1 for x < xm):
      E[min(max(X - a, 0), L)] = integral_a^{a+L} S(x) dx
    For a >= xm and alpha != 1:
      = xm^alpha / (1 - alpha) * ((a + L)^(1 - alpha) - a^(1 - alpha))
    Multiplied by Poisson annual frequency `lam`.
    """
    assert attachment >= xm and alpha > 1.0
    integral = (xm ** alpha) / (1.0 - alpha) * ((attachment + limit) ** (1.0 - alpha) - attachment ** (1.0 - alpha))
    return lam * integral
