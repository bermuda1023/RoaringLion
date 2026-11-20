use rayon::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OccXlParams {
    pub occ_limit: f64,
    pub occ_attachment: f64,
    pub occ_franchise: f64,
    /// None = unlimited reinstatements; Some(k) => total annual limit = (1.0 + k) * occ_limit
    pub reinstatements: Option<f64>,
    /// Reinstatement premium rate as fraction of deposit premium per full limit reinstated (e.g. 1.0 = 100% pro-rata)
    pub rip_rate: f64,
    pub deposit_premium: f64,
    pub placed_share: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AggXlParams {
    pub agg_limit: f64,
    pub agg_attachment: f64,
    pub per_event_franchise: f64,
    pub per_event_cap: Option<f64>,
    pub placed_share: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QuotaShareParams {
    pub cession_share: f64,
    pub event_cap: Option<f64>,
    /// Optional annual aggregate cession cap (enables AggregateQuotaShare)
    pub agg_cap: Option<f64>,
    pub placed_share: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SurplusShareParams {
    pub retention_line: f64,
    pub num_lines: f64,
    pub placed_share: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventOrderParams {
    /// 1-indexed inclusive min event order within trial (e.g. 2 for 2nd+ events)
    pub min_order: usize,
    /// 1-indexed inclusive max event order within trial (None = unbounded)
    pub max_order: Option<usize>,
    /// Minimum event loss required to count toward event order
    pub min_loss_threshold: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CurvePoint {
    pub loss_threshold: f64,
    pub factor: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UpliftRule {
    /// -1 means wildcard (applies to all region_peril_ids)
    pub region_peril_id: i32,
    pub flat_severity_factor: f64,
    /// Optional piecewise-linear curve uplift sorted by loss_threshold ascending
    pub curve_points: Vec<CurvePoint>,
    pub additive_load: f64,
    pub min_loss: Option<f64>,
    pub max_loss: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LayerStateSnapshot {
    pub remaining_agg_limit: f64,
    pub agg_deductible_eroded: f64,
    pub reinstatements_used: f64,
    pub reinstatements_remaining: Option<f64>,
    pub exhausted: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SingleLossStep {
    pub event_index: usize,
    pub subject_loss: f64,
    pub loss_to_layer: f64,
    pub ceded_loss: f64,
    pub rip_due: f64,
    pub state_before: LayerStateSnapshot,
    pub state_after: LayerStateSnapshot,
}

#[inline]
pub fn interpolate_curve(loss: f64, points: &[CurvePoint]) -> f64 {
    if points.is_empty() {
        return 1.0;
    }
    if loss <= points[0].loss_threshold {
        return points[0].factor;
    }
    let last = &points[points.len() - 1];
    if loss >= last.loss_threshold {
        return last.factor;
    }
    for w in points.windows(2) {
        let p0 = &w[0];
        let p1 = &w[1];
        if loss >= p0.loss_threshold && loss <= p1.loss_threshold {
            let span = p1.loss_threshold - p0.loss_threshold;
            if span <= 0.0 {
                return p1.factor;
            }
            let t = (loss - p0.loss_threshold) / span;
            return p0.factor + t * (p1.factor - p0.factor);
        }
    }
    1.0
}

#[inline]
pub fn apply_uplift_scalar(loss: f64, rp_id: i32, rules: &[UpliftRule]) -> f64 {
    if loss <= 0.0 {
        return 0.0;
    }
    let mut out = loss;
    for r in rules {
        if r.region_peril_id == -1 || r.region_peril_id == rp_id {
            let curve_f = interpolate_curve(out, &r.curve_points);
            out = out * r.flat_severity_factor * curve_f + r.additive_load;
            if let Some(mn) = r.min_loss {
                if out < mn {
                    out = mn;
                }
            }
            if let Some(mx) = r.max_loss {
                if out > mx {
                    out = mx;
                }
            }
        }
    }
    if out < 0.0 {
        0.0
    } else {
        out
    }
}

/// Core single-period stateful OccXL pass.
/// Shared verbatim between stochastic YELT evaluation and deterministic / claims runs.
#[inline]
pub fn eval_occ_xl_period(
    losses: &[f64],
    params: &OccXlParams,
    out_ceded: &mut [f64],
) -> (f64, f64, f64) {
    let annual_cap = match params.reinstatements {
        Some(k) => (1.0 + k.max(0.0)) * params.occ_limit,
        None => f64::INFINITY,
    };
    let mut cum_layer_loss = 0.0_f64;
    let mut total_ceded = 0.0_f64;
    let mut max_occ_ceded = 0.0_f64;
    let mut total_rip = 0.0_f64;

    for (i, &gross) in losses.iter().enumerate() {
        if gross <= 0.0 || gross < params.occ_franchise {
            out_ceded[i] = 0.0;
            continue;
        }
        let raw_layer = (gross - params.occ_attachment).clamp(0.0, params.occ_limit);
        let remaining = (annual_cap - cum_layer_loss).max(0.0);
        let in_layer = raw_layer.min(remaining);
        cum_layer_loss += in_layer;

        // Pro-rata reinstatement premium applies to limit consumed up to reinstatable amount
        if in_layer > 0.0 && params.rip_rate > 0.0 && params.occ_limit > 0.0 {
            let reinstatable_cap = match params.reinstatements {
                Some(k) => k.max(0.0) * params.occ_limit,
                None => f64::INFINITY,
            };
            let prev_cum = cum_layer_loss - in_layer;
            let prev_reinstated = prev_cum.min(reinstatable_cap);
            let new_reinstated = cum_layer_loss.min(reinstatable_cap);
            let newly_reinstated = (new_reinstated - prev_reinstated).max(0.0);
            total_rip += (newly_reinstated / params.occ_limit)
                * params.rip_rate
                * params.deposit_premium
                * params.placed_share;
        }

        let ceded = in_layer * params.placed_share;
        out_ceded[i] = ceded;
        total_ceded += ceded;
        if ceded > max_occ_ceded {
            max_occ_ceded = ceded;
        }
    }

    (total_ceded, max_occ_ceded, total_rip)
}

/// Core single-period stateful AggXL pass.
/// Shared verbatim between stochastic YELT evaluation and deterministic / claims runs.
#[inline]
pub fn eval_agg_xl_period(
    losses: &[f64],
    params: &AggXlParams,
    out_ceded: &mut [f64],
) -> (f64, f64) {
    let mut cum_subject = 0.0_f64;
    let mut cum_layer = 0.0_f64;
    let mut total_ceded = 0.0_f64;
    let mut max_occ_ceded = 0.0_f64;

    for (i, &gross) in losses.iter().enumerate() {
        if gross <= 0.0 || gross < params.per_event_franchise {
            out_ceded[i] = 0.0;
            continue;
        }
        let capped = match params.per_event_cap {
            Some(c) => gross.min(c),
            None => gross,
        };
        cum_subject += capped;
        let target_layer = (cum_subject - params.agg_attachment).clamp(0.0, params.agg_limit);
        let in_layer = (target_layer - cum_layer).max(0.0);
        cum_layer += in_layer;

        let ceded = in_layer * params.placed_share;
        out_ceded[i] = ceded;
        total_ceded += ceded;
        if ceded > max_occ_ceded {
            max_occ_ceded = ceded;
        }
    }

    (total_ceded, max_occ_ceded)
}

/// Detailed deterministic trace for OccXL using identical logic to eval_occ_xl_period.
pub fn trace_occ_xl_deterministic(losses: &[f64], params: &OccXlParams) -> Vec<SingleLossStep> {
    let annual_cap = match params.reinstatements {
        Some(k) => (1.0 + k.max(0.0)) * params.occ_limit,
        None => f64::INFINITY,
    };
    let reinstatable_cap = match params.reinstatements {
        Some(k) => k.max(0.0) * params.occ_limit,
        None => f64::INFINITY,
    };
    let mut cum_layer_loss = 0.0_f64;
    let mut steps = Vec::with_capacity(losses.len());

    for (i, &gross) in losses.iter().enumerate() {
        let rem_before = if annual_cap.is_finite() {
            (annual_cap - cum_layer_loss).max(0.0)
        } else {
            f64::INFINITY
        };
        let used_reinst_before = if params.occ_limit > 0.0 {
            (cum_layer_loss.min(reinstatable_cap)) / params.occ_limit
        } else {
            0.0
        };
        let state_before = LayerStateSnapshot {
            remaining_agg_limit: if rem_before.is_finite() { rem_before } else { -1.0 },
            agg_deductible_eroded: 0.0,
            reinstatements_used: used_reinst_before,
            reinstatements_remaining: params.reinstatements.map(|k| (k - used_reinst_before).max(0.0)),
            exhausted: annual_cap.is_finite() && cum_layer_loss >= annual_cap - 1e-9,
        };

        let mut in_layer = 0.0;
        let mut rip_due = 0.0;
        if gross > 0.0 && gross >= params.occ_franchise {
            let raw_layer = (gross - params.occ_attachment).clamp(0.0, params.occ_limit);
            let remaining = (annual_cap - cum_layer_loss).max(0.0);
            in_layer = raw_layer.min(remaining);
            let prev_cum = cum_layer_loss;
            cum_layer_loss += in_layer;

            if in_layer > 0.0 && params.rip_rate > 0.0 && params.occ_limit > 0.0 {
                let prev_r = prev_cum.min(reinstatable_cap);
                let new_r = cum_layer_loss.min(reinstatable_cap);
                let newly_r = (new_r - prev_r).max(0.0);
                rip_due = (newly_r / params.occ_limit)
                    * params.rip_rate
                    * params.deposit_premium
                    * params.placed_share;
            }
        }

        let rem_after = if annual_cap.is_finite() {
            (annual_cap - cum_layer_loss).max(0.0)
        } else {
            f64::INFINITY
        };
        let used_reinst_after = if params.occ_limit > 0.0 {
            (cum_layer_loss.min(reinstatable_cap)) / params.occ_limit
        } else {
            0.0
        };
        let state_after = LayerStateSnapshot {
            remaining_agg_limit: if rem_after.is_finite() { rem_after } else { -1.0 },
            agg_deductible_eroded: 0.0,
            reinstatements_used: used_reinst_after,
            reinstatements_remaining: params.reinstatements.map(|k| (k - used_reinst_after).max(0.0)),
            exhausted: annual_cap.is_finite() && cum_layer_loss >= annual_cap - 1e-9,
        };

        steps.push(SingleLossStep {
            event_index: i,
            subject_loss: gross,
            loss_to_layer: in_layer,
            ceded_loss: in_layer * params.placed_share,
            rip_due,
            state_before,
            state_after,
        });
    }

    steps
}

/// Detailed deterministic trace for AggXL using identical logic to eval_agg_xl_period.
pub fn trace_agg_xl_deterministic(losses: &[f64], params: &AggXlParams) -> Vec<SingleLossStep> {
    let mut cum_subject = 0.0_f64;
    let mut cum_layer = 0.0_f64;
    let mut steps = Vec::with_capacity(losses.len());

    for (i, &gross) in losses.iter().enumerate() {
        let state_before = LayerStateSnapshot {
            remaining_agg_limit: (params.agg_limit - cum_layer).max(0.0),
            agg_deductible_eroded: cum_subject.min(params.agg_attachment),
            reinstatements_used: 0.0,
            reinstatements_remaining: None,
            exhausted: cum_layer >= params.agg_limit - 1e-9,
        };

        let mut in_layer = 0.0;
        if gross > 0.0 && gross >= params.per_event_franchise {
            let capped = match params.per_event_cap {
                Some(c) => gross.min(c),
                None => gross,
            };
            cum_subject += capped;
            let target_layer = (cum_subject - params.agg_attachment).clamp(0.0, params.agg_limit);
            in_layer = (target_layer - cum_layer).max(0.0);
            cum_layer += in_layer;
        }

        let state_after = LayerStateSnapshot {
            remaining_agg_limit: (params.agg_limit - cum_layer).max(0.0),
            agg_deductible_eroded: cum_subject.min(params.agg_attachment),
            reinstatements_used: 0.0,
            reinstatements_remaining: None,
            exhausted: cum_layer >= params.agg_limit - 1e-9,
        };

        steps.push(SingleLossStep {
            event_index: i,
            subject_loss: gross,
            loss_to_layer: in_layer,
            ceded_loss: in_layer * params.placed_share,
            rip_due: 0.0,
            state_before,
            state_after,
        });
    }

    steps
}

/// Compute trial CSR offsets `[0..=num_trials]` for a YELT sorted by `trial_id` (1-indexed: 1..=num_trials).
pub fn build_trial_offsets(trial_ids: &[u32], num_trials: usize) -> Vec<usize> {
    let mut counts = vec![0usize; num_trials];
    for &t in trial_ids {
        if t >= 1 && (t as usize) <= num_trials {
            counts[(t - 1) as usize] += 1;
        }
    }
    let mut offsets = Vec::with_capacity(num_trials + 1);
    let mut acc = 0usize;
    offsets.push(0);
    for c in counts {
        acc += c;
        offsets.push(acc);
    }
    offsets
}

/// Parallel OccXL evaluation across all trials using Rayon.
/// Input `trial_ids` MUST be sorted ascending (1..=num_trials) and chronologically within each trial.
pub fn eval_occ_xl_parallel(
    trial_ids: &[u32],
    losses: &[f64],
    num_trials: usize,
    params: &OccXlParams,
) -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>) {
    let offsets = build_trial_offsets(trial_ids, num_trials);
    let mut event_ceded = vec![0.0_f64; losses.len()];
    let mut annual_ceded = vec![0.0_f64; num_trials];
    let mut max_occ = vec![0.0_f64; num_trials];
    let mut annual_rip = vec![0.0_f64; num_trials];

    // Partition event_ceded mutably per trial via chunked parallel processing
    // To avoid overlapping mutable slices cleanly, we parallelize over trial chunks
    let chunk_size = (num_trials / (rayon::current_num_threads() * 4).max(1)).max(256);

    let results: Vec<(usize, usize, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>)> = (0..num_trials)
        .step_by(chunk_size)
        .collect::<Vec<_>>()
        .into_par_iter()
        .map(|t_start| {
            let t_end = (t_start + chunk_size).min(num_trials);
            let ev_start = offsets[t_start];
            let ev_end = offsets[t_end];
            let mut local_ev = vec![0.0_f64; ev_end - ev_start];
            let mut local_ann = vec![0.0_f64; t_end - t_start];
            let mut local_max = vec![0.0_f64; t_end - t_start];
            let mut local_rip = vec![0.0_f64; t_end - t_start];

            for t in t_start..t_end {
                let s = offsets[t];
                let e = offsets[t + 1];
                if s < e {
                    let (ann, mx, rip) = eval_occ_xl_period(
                        &losses[s..e],
                        params,
                        &mut local_ev[(s - ev_start)..(e - ev_start)],
                    );
                    local_ann[t - t_start] = ann;
                    local_max[t - t_start] = mx;
                    local_rip[t - t_start] = rip;
                }
            }
            (t_start, ev_start, local_ev, local_ann, local_max, local_rip)
        })
        .collect();

    for (t_start, ev_start, local_ev, local_ann, local_max, local_rip) in results {
        let ev_len = local_ev.len();
        if ev_len > 0 {
            event_ceded[ev_start..(ev_start + ev_len)].copy_from_slice(&local_ev);
        }
        let t_len = local_ann.len();
        annual_ceded[t_start..(t_start + t_len)].copy_from_slice(&local_ann);
        max_occ[t_start..(t_start + t_len)].copy_from_slice(&local_max);
        annual_rip[t_start..(t_start + t_len)].copy_from_slice(&local_rip);
    }

    (event_ceded, annual_ceded, max_occ, annual_rip)
}

/// Parallel AggXL evaluation across all trials using Rayon.
pub fn eval_agg_xl_parallel(
    trial_ids: &[u32],
    losses: &[f64],
    num_trials: usize,
    params: &AggXlParams,
) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let offsets = build_trial_offsets(trial_ids, num_trials);
    let mut event_ceded = vec![0.0_f64; losses.len()];
    let mut annual_ceded = vec![0.0_f64; num_trials];
    let mut max_occ = vec![0.0_f64; num_trials];

    let chunk_size = (num_trials / (rayon::current_num_threads() * 4).max(1)).max(256);

    let results: Vec<(usize, usize, Vec<f64>, Vec<f64>, Vec<f64>)> = (0..num_trials)
        .step_by(chunk_size)
        .collect::<Vec<_>>()
        .into_par_iter()
        .map(|t_start| {
            let t_end = (t_start + chunk_size).min(num_trials);
            let ev_start = offsets[t_start];
            let ev_end = offsets[t_end];
            let mut local_ev = vec![0.0_f64; ev_end - ev_start];
            let mut local_ann = vec![0.0_f64; t_end - t_start];
            let mut local_max = vec![0.0_f64; t_end - t_start];

            for t in t_start..t_end {
                let s = offsets[t];
                let e = offsets[t + 1];
                if s < e {
                    let (ann, mx) = eval_agg_xl_period(
                        &losses[s..e],
                        params,
                        &mut local_ev[(s - ev_start)..(e - ev_start)],
                    );
                    local_ann[t - t_start] = ann;
                    local_max[t - t_start] = mx;
                }
            }
            (t_start, ev_start, local_ev, local_ann, local_max)
        })
        .collect();

    for (t_start, ev_start, local_ev, local_ann, local_max) in results {
        let ev_len = local_ev.len();
        if ev_len > 0 {
            event_ceded[ev_start..(ev_start + ev_len)].copy_from_slice(&local_ev);
        }
        let t_len = local_ann.len();
        annual_ceded[t_start..(t_start + t_len)].copy_from_slice(&local_ann);
        max_occ[t_start..(t_start + t_len)].copy_from_slice(&local_max);
    }

    (event_ceded, annual_ceded, max_occ)
}

/// Parallel uplift evaluation across all YELT rows.
pub fn apply_uplifts_parallel(losses: &[f64], rp_ids: &[i32], rules: &[UpliftRule]) -> Vec<f64> {
    losses
        .par_iter()
        .zip(rp_ids.par_iter())
        .map(|(&l, &rp)| apply_uplift_scalar(l, rp, rules))
        .collect()
}

/// Core single-period QuotaShare / AggregateQuotaShare pass.
#[inline]
pub fn eval_quota_share_period(
    losses: &[f64],
    params: &QuotaShareParams,
    out_ceded: &mut [f64],
) -> (f64, f64) {
    let mut cum_ceded = 0.0_f64;
    let mut max_occ = 0.0_f64;
    let agg_cap = params.agg_cap.unwrap_or(f64::INFINITY);

    for (i, &gross) in losses.iter().enumerate() {
        if gross <= 0.0 {
            out_ceded[i] = 0.0;
            continue;
        }
        let mut raw = gross * params.cession_share;
        if let Some(ec) = params.event_cap {
            raw = raw.min(ec);
        }
        let remaining = (agg_cap - cum_ceded).max(0.0);
        let in_layer = raw.min(remaining);
        cum_ceded += in_layer;
        let ceded = in_layer * params.placed_share;
        out_ceded[i] = ceded;
        if ceded > max_occ {
            max_occ = ceded;
        }
    }
    (cum_ceded * params.placed_share, max_occ)
}

pub fn eval_quota_share_parallel(
    trial_ids: &[u32],
    losses: &[f64],
    num_trials: usize,
    params: &QuotaShareParams,
) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let offsets = build_trial_offsets(trial_ids, num_trials);
    let mut event_ceded = vec![0.0_f64; losses.len()];
    let mut annual_ceded = vec![0.0_f64; num_trials];
    let mut max_occ = vec![0.0_f64; num_trials];
    let chunk_size = (num_trials / (rayon::current_num_threads() * 4).max(1)).max(256);

    let results: Vec<(usize, usize, Vec<f64>, Vec<f64>, Vec<f64>)> = (0..num_trials)
        .step_by(chunk_size)
        .collect::<Vec<_>>()
        .into_par_iter()
        .map(|t_start| {
            let t_end = (t_start + chunk_size).min(num_trials);
            let ev_start = offsets[t_start];
            let ev_end = offsets[t_end];
            let mut local_ev = vec![0.0_f64; ev_end - ev_start];
            let mut local_ann = vec![0.0_f64; t_end - t_start];
            let mut local_max = vec![0.0_f64; t_end - t_start];
            for t in t_start..t_end {
                let s = offsets[t];
                let e = offsets[t + 1];
                if s < e {
                    let (ann, mx) = eval_quota_share_period(
                        &losses[s..e],
                        params,
                        &mut local_ev[(s - ev_start)..(e - ev_start)],
                    );
                    local_ann[t - t_start] = ann;
                    local_max[t - t_start] = mx;
                }
            }
            (t_start, ev_start, local_ev, local_ann, local_max)
        })
        .collect();

    for (t_start, ev_start, local_ev, local_ann, local_max) in results {
        if !local_ev.is_empty() {
            event_ceded[ev_start..(ev_start + local_ev.len())].copy_from_slice(&local_ev);
        }
        annual_ceded[t_start..(t_start + local_ann.len())].copy_from_slice(&local_ann);
        max_occ[t_start..(t_start + local_max.len())].copy_from_slice(&local_max);
    }
    (event_ceded, annual_ceded, max_occ)
}

/// Parallel Surplus Share: cedes excess over retention_line up to (retention_line * num_lines).
pub fn eval_surplus_share_parallel(
    trial_ids: &[u32],
    losses: &[f64],
    num_trials: usize,
    params: &SurplusShareParams,
) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
    let max_cession = (params.retention_line * params.num_lines).max(0.0);
    let occ_equiv = OccXlParams {
        occ_limit: max_cession,
        occ_attachment: params.retention_line.max(0.0),
        occ_franchise: 0.0,
        reinstatements: None,
        rip_rate: 0.0,
        deposit_premium: 0.0,
        placed_share: params.placed_share,
    };
    let (ev, ann, mx, _rip) = eval_occ_xl_parallel(trial_ids, losses, num_trials, &occ_equiv);
    (ev, ann, mx)
}

/// Parallel Event-Order Filter: keeps only events whose chronological rank within the trial
/// (among events >= min_loss_threshold) falls in [min_order, max_order].
pub fn eval_event_order_filter_parallel(
    trial_ids: &[u32],
    losses: &[f64],
    num_trials: usize,
    params: &EventOrderParams,
) -> Vec<f64> {
    let offsets = build_trial_offsets(trial_ids, num_trials);
    let mut out = vec![0.0_f64; losses.len()];
    let chunk_size = (num_trials / (rayon::current_num_threads() * 4).max(1)).max(256);

    let results: Vec<(usize, Vec<f64>)> = (0..num_trials)
        .step_by(chunk_size)
        .collect::<Vec<_>>()
        .into_par_iter()
        .map(|t_start| {
            let t_end = (t_start + chunk_size).min(num_trials);
            let ev_start = offsets[t_start];
            let ev_end = offsets[t_end];
            let mut local_ev = vec![0.0_f64; ev_end - ev_start];
            for t in t_start..t_end {
                let s = offsets[t];
                let e = offsets[t + 1];
                let mut rank = 0usize;
                for i in s..e {
                    let g = losses[i];
                    if g > 0.0 && g >= params.min_loss_threshold {
                        rank += 1;
                        let in_max = params.max_order.map(|m| rank <= m).unwrap_or(true);
                        if rank >= params.min_order && in_max {
                            local_ev[i - ev_start] = g;
                        }
                    }
                }
            }
            (ev_start, local_ev)
        })
        .collect();

    for (ev_start, local_ev) in results {
        if !local_ev.is_empty() {
            out[ev_start..(ev_start + local_ev.len())].copy_from_slice(&local_ev);
        }
    }
    out
}

/// Single-period Cascading / Drop-Down Tower:
/// As lower layers exhaust their annual limits, upper layers drop down to attach immediately above
/// the remaining active retention + remaining underlying limit.
pub fn eval_cascading_period(
    losses: &[f64],
    layers: &[OccXlParams],
    out_total_ceded: &mut [f64],
) -> (f64, f64, f64) {
    let m = layers.len();
    let mut cum_used = vec![0.0_f64; m];
    let annual_caps: Vec<f64> = layers
        .iter()
        .map(|p| match p.reinstatements {
            Some(k) => (1.0 + k.max(0.0)) * p.occ_limit,
            None => f64::INFINITY,
        })
        .collect();
    let base_retention = layers.first().map(|l| l.occ_attachment).unwrap_or(0.0);
    let mut total_ceded = 0.0_f64;
    let mut max_occ = 0.0_f64;
    let mut total_rip = 0.0_f64;

    for (ev_idx, &gross) in losses.iter().enumerate() {
        let mut ev_ceded = 0.0_f64;
        if gross > base_retention {
            let mut cursor = base_retention;
            for l in 0..m {
                let p = &layers[l];
                let rem_cap = (annual_caps[l] - cum_used[l]).max(0.0);
                if rem_cap <= 1e-12 {
                    continue;
                }
                let avail_this_occ = p.occ_limit.min(rem_cap);
                let in_layer = (gross - cursor).clamp(0.0, avail_this_occ);
                if in_layer > 0.0 {
                    let prev_cum = cum_used[l];
                    cum_used[l] += in_layer;
                    if p.rip_rate > 0.0 && p.occ_limit > 0.0 {
                        let r_cap = match p.reinstatements {
                            Some(k) => k.max(0.0) * p.occ_limit,
                            None => f64::INFINITY,
                        };
                        let newly_r = (cum_used[l].min(r_cap) - prev_cum.min(r_cap)).max(0.0);
                        total_rip += (newly_r / p.occ_limit) * p.rip_rate * p.deposit_premium * p.placed_share;
                    }
                    ev_ceded += in_layer * p.placed_share;
                    cursor += in_layer;
                } else {
                    cursor += avail_this_occ;
                }
                if cursor >= gross {
                    break;
                }
            }
        }
        out_total_ceded[ev_idx] = ev_ceded;
        total_ceded += ev_ceded;
        if ev_ceded > max_occ {
            max_occ = ev_ceded;
        }
    }
    (total_ceded, max_occ, total_rip)
}

pub fn eval_cascading_parallel(
    trial_ids: &[u32],
    losses: &[f64],
    num_trials: usize,
    layers: &[OccXlParams],
) -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>) {
    let offsets = build_trial_offsets(trial_ids, num_trials);
    let mut event_ceded = vec![0.0_f64; losses.len()];
    let mut annual_ceded = vec![0.0_f64; num_trials];
    let mut max_occ = vec![0.0_f64; num_trials];
    let mut annual_rip = vec![0.0_f64; num_trials];
    let chunk_size = (num_trials / (rayon::current_num_threads() * 4).max(1)).max(256);

    let results: Vec<(usize, usize, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>)> = (0..num_trials)
        .step_by(chunk_size)
        .collect::<Vec<_>>()
        .into_par_iter()
        .map(|t_start| {
            let t_end = (t_start + chunk_size).min(num_trials);
            let ev_start = offsets[t_start];
            let ev_end = offsets[t_end];
            let mut local_ev = vec![0.0_f64; ev_end - ev_start];
            let mut local_ann = vec![0.0_f64; t_end - t_start];
            let mut local_max = vec![0.0_f64; t_end - t_start];
            let mut local_rip = vec![0.0_f64; t_end - t_start];
            for t in t_start..t_end {
                let s = offsets[t];
                let e = offsets[t + 1];
                if s < e {
                    let (ann, mx, rip) = eval_cascading_period(
                        &losses[s..e],
                        layers,
                        &mut local_ev[(s - ev_start)..(e - ev_start)],
                    );
                    local_ann[t - t_start] = ann;
                    local_max[t - t_start] = mx;
                    local_rip[t - t_start] = rip;
                }
            }
            (t_start, ev_start, local_ev, local_ann, local_max, local_rip)
        })
        .collect();

    for (t_start, ev_start, local_ev, local_ann, local_max, local_rip) in results {
        if !local_ev.is_empty() {
            event_ceded[ev_start..(ev_start + local_ev.len())].copy_from_slice(&local_ev);
        }
        annual_ceded[t_start..(t_start + local_ann.len())].copy_from_slice(&local_ann);
        max_occ[t_start..(t_start + local_max.len())].copy_from_slice(&local_max);
        annual_rip[t_start..(t_start + local_rip.len())].copy_from_slice(&local_rip);
    }
    (event_ceded, annual_ceded, max_occ, annual_rip)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_occ_xl_reinstatement_and_parity() {
        let params = OccXlParams {
            occ_limit: 50.0,
            occ_attachment: 100.0,
            occ_franchise: 0.0,
            reinstatements: Some(1.0),
            rip_rate: 1.0,
            deposit_premium: 10.0,
            placed_share: 0.5,
        };
        // Three events in one year: 130 (30 in layer), 160 (50 in layer -> 80 cum), 150 (50 raw -> capped at 20 remaining)
        let losses = vec![130.0, 160.0, 150.0];
        let mut out = vec![0.0; 3];
        let (ann, mx, rip) = eval_occ_xl_period(&losses, &params, &mut out);
        assert!((out[0] - 15.0).abs() < 1e-9);
        assert!((out[1] - 25.0).abs() < 1e-9);
        assert!((out[2] - 10.0).abs() < 1e-9);
        assert!((ann - 50.0).abs() < 1e-9);
        assert!((mx - 25.0).abs() < 1e-9);
        // 1 full reinstatement (50) reinstated => 1.0 * 1.0 * 10.0 * 0.5 = 5.0
        assert!((rip - 5.0).abs() < 1e-9);

        // Deterministic trace must match stochastic period pass identically
        let trace = trace_occ_xl_deterministic(&losses, &params);
        let trace_ann: f64 = trace.iter().map(|s| s.ceded_loss).sum();
        let trace_rip: f64 = trace.iter().map(|s| s.rip_due).sum();
        assert!((trace_ann - ann).abs() < 1e-9);
        assert!((trace_rip - rip).abs() < 1e-9);
        assert!(trace[2].state_after.exhausted);
    }

    #[test]
    fn test_agg_xl_erosion_and_parity() {
        let params = AggXlParams {
            agg_limit: 50.0,
            agg_attachment: 50.0,
            per_event_franchise: 5.0,
            per_event_cap: Some(40.0),
            placed_share: 1.0,
        };
        // Events: 4 (below franchise -> 0), 30 (cum 30, 0 in layer), 45 (capped 40 -> cum 70, 20 in layer), 40 (cum 110 -> 30 remaining in layer)
        let losses = vec![4.0, 30.0, 45.0, 40.0];
        let mut out = vec![0.0; 4];
        let (ann, mx) = eval_agg_xl_period(&losses, &params, &mut out);
        assert!((out[0] - 0.0).abs() < 1e-9);
        assert!((out[1] - 0.0).abs() < 1e-9);
        assert!((out[2] - 20.0).abs() < 1e-9);
        assert!((out[3] - 30.0).abs() < 1e-9);
        assert!((ann - 50.0).abs() < 1e-9);
        assert!((mx - 30.0).abs() < 1e-9);

        let trace = trace_agg_xl_deterministic(&losses, &params);
        let trace_ann: f64 = trace.iter().map(|s| s.ceded_loss).sum();
        assert!((trace_ann - ann).abs() < 1e-9);
        assert!(trace[3].state_after.exhausted);
    }

    #[test]
    fn test_agg_quota_share_and_event_order_and_cascading() {
        // Aggregate Quota Share: 25% share, event cap 15, agg cap 25
        let qs = QuotaShareParams {
            cession_share: 0.25,
            event_cap: Some(15.0),
            agg_cap: Some(25.0),
            placed_share: 1.0,
        };
        let mut qs_out = vec![0.0; 3];
        let (qs_ann, _) = eval_quota_share_period(&[80.0, 60.0, 40.0], &qs, &mut qs_out);
        // 80*0.25=20->cap 15; 60*0.25=15->capped to 10 remaining; 40->0
        assert!((qs_out[0] - 15.0).abs() < 1e-9);
        assert!((qs_out[1] - 10.0).abs() < 1e-9);
        assert!((qs_out[2] - 0.0).abs() < 1e-9);
        assert!((qs_ann - 25.0).abs() < 1e-9);

        // Event order filter: keep 2nd+ events >= 10
        let eo = EventOrderParams {
            min_order: 2,
            max_order: None,
            min_loss_threshold: 10.0,
        };
        let filtered = eval_event_order_filter_parallel(&[1, 1, 1], &[5.0, 20.0, 30.0], 1, &eo);
        assert_eq!(filtered, vec![0.0, 0.0, 30.0]);

        // Cascading tower: Layer 1 = 30 xs 50 (0 reinst), Layer 2 = 40 xs 80 (0 reinst)
        // Event 1 = 80 -> Layer 1 exhausts (30). Event 2 = 70 -> Layer 2 drops down to 50 and pays 20!
        let layers = vec![
            OccXlParams {
                occ_limit: 30.0,
                occ_attachment: 50.0,
                occ_franchise: 0.0,
                reinstatements: Some(0.0),
                rip_rate: 0.0,
                deposit_premium: 5.0,
                placed_share: 1.0,
            },
            OccXlParams {
                occ_limit: 40.0,
                occ_attachment: 80.0,
                occ_franchise: 0.0,
                reinstatements: Some(0.0),
                rip_rate: 0.0,
                deposit_premium: 4.0,
                placed_share: 1.0,
            },
        ];
        let mut casc_out = vec![0.0; 2];
        let (c_ann, _, _) = eval_cascading_period(&[80.0, 70.0], &layers, &mut casc_out);
        assert!((casc_out[0] - 30.0).abs() < 1e-9);
        assert!((casc_out[1] - 20.0).abs() < 1e-9);
        assert!((c_ann - 50.0).abs() < 1e-9);
    }
}
