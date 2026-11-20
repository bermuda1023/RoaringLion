pub mod metrics;
pub mod terms;

#[cfg(feature = "python")]
mod py_bindings {
    use numpy::{IntoPyArray, PyArray1, PyReadonlyArray1, PyReadonlyArray2, PyUntypedArrayMethods};
    use pyo3::prelude::*;

    use crate::metrics::{compute_ep_stats, compute_euler_cotvar};
    use crate::terms::{
        apply_uplifts_parallel, eval_agg_xl_parallel, eval_occ_xl_parallel,
        trace_agg_xl_deterministic, trace_occ_xl_deterministic, AggXlParams, OccXlParams,
        UpliftRule,
    };

    #[pyfunction]
    fn apply_uplifts_yelt<'py>(
        py: Python<'py>,
        losses: PyReadonlyArray1<'py, f64>,
        rp_ids: PyReadonlyArray1<'py, i32>,
        rules_json: &str,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        let rules: Vec<UpliftRule> = serde_json::from_str(rules_json)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
        let l_slice = losses.as_slice()?;
        let rp_slice = rp_ids.as_slice()?;
        let out = py.allow_threads(|| apply_uplifts_parallel(l_slice, rp_slice, &rules));
        Ok(out.into_pyarray_bound(py))
    }

    #[pyfunction]
    fn eval_occ_xl_yelt<'py>(
        py: Python<'py>,
        trial_ids: PyReadonlyArray1<'py, u32>,
        losses: PyReadonlyArray1<'py, f64>,
        num_trials: usize,
        params_json: &str,
    ) -> PyResult<(
        Bound<'py, PyArray1<f64>>,
        Bound<'py, PyArray1<f64>>,
        Bound<'py, PyArray1<f64>>,
        Bound<'py, PyArray1<f64>>,
    )> {
        let params: OccXlParams = serde_json::from_str(params_json)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
        let t_slice = trial_ids.as_slice()?;
        let l_slice = losses.as_slice()?;
        let (ev, ann, mx, rip) =
            py.allow_threads(|| eval_occ_xl_parallel(t_slice, l_slice, num_trials, &params));
        Ok((
            ev.into_pyarray_bound(py),
            ann.into_pyarray_bound(py),
            mx.into_pyarray_bound(py),
            rip.into_pyarray_bound(py),
        ))
    }

    #[pyfunction]
    fn eval_agg_xl_yelt<'py>(
        py: Python<'py>,
        trial_ids: PyReadonlyArray1<'py, u32>,
        losses: PyReadonlyArray1<'py, f64>,
        num_trials: usize,
        params_json: &str,
    ) -> PyResult<(
        Bound<'py, PyArray1<f64>>,
        Bound<'py, PyArray1<f64>>,
        Bound<'py, PyArray1<f64>>,
    )> {
        let params: AggXlParams = serde_json::from_str(params_json)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
        let t_slice = trial_ids.as_slice()?;
        let l_slice = losses.as_slice()?;
        let (ev, ann, mx) =
            py.allow_threads(|| eval_agg_xl_parallel(t_slice, l_slice, num_trials, &params));
        Ok((
            ev.into_pyarray_bound(py),
            ann.into_pyarray_bound(py),
            mx.into_pyarray_bound(py),
        ))
    }

    #[pyfunction]
    fn trace_occ_xl(losses: Vec<f64>, params_json: &str) -> PyResult<String> {
        let params: OccXlParams = serde_json::from_str(params_json)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
        let steps = trace_occ_xl_deterministic(&losses, &params);
        serde_json::to_string(&steps)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    }

    #[pyfunction]
    fn trace_agg_xl(losses: Vec<f64>, params_json: &str) -> PyResult<String> {
        let params: AggXlParams = serde_json::from_str(params_json)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
        let steps = trace_agg_xl_deterministic(&losses, &params);
        serde_json::to_string(&steps)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    }

    #[pyfunction]
    fn ep_stats<'py>(
        py: Python<'py>,
        values: PyReadonlyArray1<'py, f64>,
        return_periods: Vec<f64>,
    ) -> PyResult<String> {
        let v_slice = values.as_slice()?;
        let stats = py.allow_threads(|| compute_ep_stats(v_slice, &return_periods));
        serde_json::to_string(&stats)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
    }

    #[pyfunction]
    fn euler_cotvar<'py>(
        py: Python<'py>,
        layer_matrix: PyReadonlyArray2<'py, f64>,
        return_period: f64,
    ) -> PyResult<(f64, f64, Bound<'py, PyArray1<f64>>)> {
        let shape = layer_matrix.shape();
        let num_layers = shape[0];
        let num_trials = shape[1];
        let slice = layer_matrix.as_slice()?;
        let (var, tvar, co) = py
            .allow_threads(|| compute_euler_cotvar(slice, num_layers, num_trials, return_period));
        Ok((var, tvar, co.into_pyarray_bound(py)))
    }

    #[pymodule]
    fn roaring_lion_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(apply_uplifts_yelt, m)?)?;
        m.add_function(wrap_pyfunction!(eval_occ_xl_yelt, m)?)?;
        m.add_function(wrap_pyfunction!(eval_agg_xl_yelt, m)?)?;
        m.add_function(wrap_pyfunction!(trace_occ_xl, m)?)?;
        m.add_function(wrap_pyfunction!(trace_agg_xl, m)?)?;
        m.add_function(wrap_pyfunction!(ep_stats, m)?)?;
        m.add_function(wrap_pyfunction!(euler_cotvar, m)?)?;
        Ok(())
    }
}
