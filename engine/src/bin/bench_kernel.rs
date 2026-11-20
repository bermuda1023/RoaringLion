use roaring_lion_engine::metrics::{compute_ep_stats, compute_euler_cotvar};
use roaring_lion_engine::terms::{
    eval_agg_xl_parallel, eval_occ_xl_parallel, AggXlParams, OccXlParams,
};
use std::time::Instant;

fn main() {
    let num_trials: usize = 100_000;
    let avg_events_per_trial: usize = 3;
    let total_events = num_trials * avg_events_per_trial;

    // Deterministic LCG synthetic 100k-trial YELT (Pareto-like severity)
    let mut trial_ids = Vec::with_capacity(total_events);
    let mut losses = Vec::with_capacity(total_events);
    let mut seed: u64 = 0x5EED_CAFE_1234_5678;
    for t in 1..=(num_trials as u32) {
        for _ in 0..avg_events_per_trial {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            let u = ((seed >> 11) as f64) / ((1u64 << 53) as f64);
            let loss = 10.0 * (1.0 - u.min(0.999999)).powf(-0.45);
            trial_ids.push(t);
            losses.push(loss);
        }
    }

    let occ_params = OccXlParams {
        occ_limit: 50.0,
        occ_attachment: 100.0,
        occ_franchise: 0.0,
        reinstatements: Some(1.0),
        rip_rate: 1.0,
        deposit_premium: 8.5,
        placed_share: 1.0,
    };
    let agg_params = AggXlParams {
        agg_limit: 40.0,
        agg_attachment: 60.0,
        per_event_franchise: 5.0,
        per_event_cap: Some(50.0),
        placed_share: 1.0,
    };

    // Single layer timing
    let t0 = Instant::now();
    let (_ev, ann_occ, _mx, _rip) = eval_occ_xl_parallel(&trial_ids, &losses, num_trials, &occ_params);
    let single_occ_ms = t0.elapsed().as_secs_f64() * 1000.0;

    let t1 = Instant::now();
    let (_ev2, _ann_agg, _mx2) = eval_agg_xl_parallel(&trial_ids, &losses, num_trials, &agg_params);
    let single_agg_ms = t1.elapsed().as_secs_f64() * 1000.0;

    // 500-layer book timing (250 OccXL + 250 AggXL)
    let t2 = Instant::now();
    let mut sample_layers = Vec::with_capacity(50 * num_trials);
    for i in 0..250 {
        let mut p = occ_params.clone();
        p.occ_attachment = 80.0 + (i as f64) * 0.5;
        let (_e, a, _m, _r) = eval_occ_xl_parallel(&trial_ids, &losses, num_trials, &p);
        if i < 25 {
            sample_layers.extend_from_slice(&a);
        }
    }
    for i in 0..250 {
        let mut p = agg_params.clone();
        p.agg_attachment = 50.0 + (i as f64) * 0.4;
        let (_e, a, _m) = eval_agg_xl_parallel(&trial_ids, &losses, num_trials, &p);
        if i < 25 {
            sample_layers.extend_from_slice(&a);
        }
    }
    let book_500_ms = t2.elapsed().as_secs_f64() * 1000.0;

    let t3 = Instant::now();
    let stats = compute_ep_stats(&ann_occ, &[10.0, 20.0, 50.0, 100.0, 250.0, 500.0]);
    let ep_ms = t3.elapsed().as_secs_f64() * 1000.0;

    let t4 = Instant::now();
    let (_v, port_tvar, co) = compute_euler_cotvar(&sample_layers, 50, num_trials, 250.0);
    let cotvar_ms = t4.elapsed().as_secs_f64() * 1000.0;
    let co_err = (co.iter().sum::<f64>() - port_tvar).abs();

    println!(
        "{}",
        serde_json::json!({
            "kernel": "rust-rayon",
            "threads": rayon::current_num_threads(),
            "num_trials": num_trials,
            "total_events": total_events,
            "single_occ_xl_ms": (single_occ_ms * 100.0).round() / 100.0,
            "single_agg_xl_ms": (single_agg_ms * 100.0).round() / 100.0,
            "book_500_layers_ms": (book_500_ms * 100.0).round() / 100.0,
            "ep_100k_ms": (ep_ms * 100.0).round() / 100.0,
            "euler_cotvar_50x100k_ms": (cotvar_ms * 100.0).round() / 100.0,
            "cotvar_additivity_error": co_err,
            "sample_occ_el": stats.el
        })
    );
}
