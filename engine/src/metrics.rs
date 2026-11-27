use rayon::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EpPoint {
    pub return_period: f64,
    pub probability: f64,
    pub var: f64,
    pub tvar: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SummaryStats {
    pub el: f64,
    pub sd: f64,
    pub max_val: f64,
    pub ep_points: Vec<EpPoint>,
}

/// Compute mean, sd, max, and exact VaR + TVaR at given return periods from a dense N-trial vector.
pub fn compute_ep_stats(values: &[f64], return_periods: &[f64]) -> SummaryStats {
    let n = values.len();
    if n == 0 {
        return SummaryStats {
            el: 0.0,
            sd: 0.0,
            max_val: 0.0,
            ep_points: vec![],
        };
    }
    let nf = n as f64;
    let sum: f64 = values.par_iter().sum();
    let mean = sum / nf;
    let var_sum: f64 = values.par_iter().map(|&x| (x - mean) * (x - mean)).sum();
    let sd = (var_sum / nf).sqrt();

    let mut sorted = values.to_vec();
    sorted.par_sort_unstable_by(|a, b| b.total_cmp(a)); // descending: index 0 is worst trial
    let max_val = sorted[0];

    let ep_points = return_periods
        .iter()
        .map(|&rp| {
            let p = 1.0 / rp;
            // Exact tail count k = max(1, round(p * N)) for TVaR, index k-1 for VaR
            let k = ((p * nf).round() as usize).clamp(1, n);
            let var_val = sorted[k - 1];
            let tvar_val = sorted[..k].iter().sum::<f64>() / (k as f64);
            EpPoint {
                return_period: rp,
                probability: p,
                var: var_val,
                tvar: tvar_val,
            }
        })
        .collect();

    SummaryStats {
        el: mean,
        sd,
        max_val,
        ep_points,
    }
}

/// Exact Euler / Co-TVaR allocation across `num_layers` layers for `num_trials` trials.
/// `layer_matrix` is row-major `[num_layers][num_trials]`.
/// Guaranteed: sum(co_tvar[i]) == portfolio_tvar to floating-point precision.
pub fn compute_euler_cotvar(
    layer_matrix: &[f64],
    num_layers: usize,
    num_trials: usize,
    return_period: f64,
) -> (f64, f64, Vec<f64>) {
    if num_layers == 0 || num_trials == 0 {
        return (0.0, 0.0, vec![]);
    }
    // Portfolio total per trial
    let mut port = vec![0.0_f64; num_trials];
    for l in 0..num_layers {
        let row = &layer_matrix[l * num_trials..(l + 1) * num_trials];
        for t in 0..num_trials {
            port[t] += row[t];
        }
    }

    let mut idx: Vec<usize> = (0..num_trials).collect();
    idx.par_sort_unstable_by(|&a, &b| port[b].total_cmp(&port[a]));

    let p = 1.0 / return_period;
    let k = ((p * (num_trials as f64)).round() as usize).clamp(1, num_trials);
    let tail_indices = &idx[..k];

    let port_var = port[idx[k - 1]];
    let port_tvar = tail_indices.iter().map(|&t| port[t]).sum::<f64>() / (k as f64);

    let co_tvars: Vec<f64> = (0..num_layers)
        .into_par_iter()
        .map(|l| {
            let row = &layer_matrix[l * num_trials..(l + 1) * num_trials];
            let s: f64 = tail_indices.iter().map(|&t| row[t]).sum();
            s / (k as f64)
        })
        .collect();

    (port_var, port_tvar, co_tvars)
}

/// Compute exact Euler Co-TVaR across multiple return periods with a single parallel sort.
pub fn compute_euler_cotvar_multi(
    layer_matrix: &[f64],
    num_layers: usize,
    num_trials: usize,
    return_periods: &[f64],
) -> Vec<(f64, f64, Vec<f64>)> {
    if num_layers == 0 || num_trials == 0 {
        return return_periods.iter().map(|_| (0.0, 0.0, vec![])).collect();
    }
    let mut port = vec![0.0_f64; num_trials];
    for l in 0..num_layers {
        let row = &layer_matrix[l * num_trials..(l + 1) * num_trials];
        for t in 0..num_trials {
            port[t] += row[t];
        }
    }

    let mut idx: Vec<usize> = (0..num_trials).collect();
    idx.par_sort_unstable_by(|&a, &b| port[b].total_cmp(&port[a]));

    return_periods
        .iter()
        .map(|&rp| {
            let p = 1.0 / rp;
            let k = ((p * (num_trials as f64)).round() as usize).clamp(1, num_trials);
            let tail_indices = &idx[..k];
            let port_var = port[idx[k - 1]];
            let port_tvar = tail_indices.iter().map(|&t| port[t]).sum::<f64>() / (k as f64);
            let co_tvars: Vec<f64> = (0..num_layers)
                .into_par_iter()
                .map(|l| {
                    let row = &layer_matrix[l * num_trials..(l + 1) * num_trials];
                    let s: f64 = tail_indices.iter().map(|&t| row[t]).sum();
                    s / (k as f64)
                })
                .collect();
            (port_var, port_tvar, co_tvars)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_euler_cotvar_additivity() {
        let num_trials = 1000;
        let num_layers = 4;
        let mut mat = vec![0.0_f64; num_layers * num_trials];
        for l in 0..num_layers {
            for t in 0..num_trials {
                mat[l * num_trials + t] = ((t * (l + 3)) % 97) as f64 * (l as f64 + 1.0);
            }
        }
        let (_var, port_tvar, co_tvars) = compute_euler_cotvar(&mat, num_layers, num_trials, 100.0);
        let sum_co: f64 = co_tvars.iter().sum();
        assert!((sum_co - port_tvar).abs() < 1e-9);

        let rps = [50.0, 100.0, 250.0];
        let multi = compute_euler_cotvar_multi(&mat, num_layers, num_trials, &rps);
        for (i, &rp) in rps.iter().enumerate() {
            let (sv, st, sc) = compute_euler_cotvar(&mat, num_layers, num_trials, rp);
            let (mv, mt, mc) = &multi[i];
            assert!((sv - mv).abs() < 1e-12);
            assert!((st - mt).abs() < 1e-12);
            assert!((mc.iter().sum::<f64>() - mt).abs() < 1e-9);
            for (a, b) in sc.iter().zip(mc.iter()) {
                assert!((a - b).abs() < 1e-12);
            }
        }
    }
}
