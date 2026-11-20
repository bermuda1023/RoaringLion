# RoaringLion

Model-agnostic catastrophe reinsurance structuring, deterministic/claims calculation, collateralized fund portfolio analytics, and constrained optimization platform — powered by a compiled **Rust (PyO3 + Rayon)** simulation kernel and **HiGHS** LP/MILP solver.

## Phase 0 First Slice (Implemented)

- **Rust Simulation Kernel (`engine/`)**: Multi-threaded (`rayon`) stateful `OccXL` (with reinstatements & pro-rata RIP), `AggXL`, per-deal curve/flat uplifts, OEP/AEP VaR & TVaR, exact additive Euler Co-TVaR allocation, and deterministic per-loss traces sharing the exact same financial-terms code.
- **Kernel & Solver Benchmark Gate (`bench/`)**: Measured Rust PyO3/Rayon vs Python loop on 100k trials × 500 layers and HiGHS LP/MILP with Rockafellar–Uryasev CVaR + semi-continuous minimum-line rules (`bench/results.json`).
- **FastAPI Backend (`backend/`)**: Synthetic 100k-trial granular region-peril simulation set (`SYNTH_CAT_V1`), CMS programmes with per-deal uplifts (`UNADJUSTED` vs `ADJUSTED`), DAG evaluator, portfolio ROC/IRR CDF + Euler Co-TVaR rollup, deterministic chronological runner, and breadcrumb run-manifest lineage ledger.
- **React + TypeScript Workbench (`frontend/`)**: Structuring DAG canvas (`@xyflow/react`), Unadjusted vs Adjusted Review Stats + live uplift editor, ROC/IRR CDF with Risk Appetite Guidelines (`echarts`), Euler Co-TVaR table, Deterministic Runner, and Benchmarks & Breadcrumb Ledger.

## Quick Start

```bash
# 1. Build Rust PyO3 kernel into .venv
cd engine && ../.venv/bin/maturin develop --release

# 2. Run backend API (port 8000)
cd ../backend && PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --reload --port 8000

# 3. Run frontend dev server (port 5173, proxies /api -> :8000)
cd ../frontend && npm install && npm run dev
```

## Verification & Benchmarks

```bash
# Rust kernel unit tests
cd engine && cargo test

# Python golden, closed-form, deterministic-stochastic parity, conservation & API tests
cd backend && PYTHONPATH=. ../.venv/bin/pytest -v

# Re-run kernel & HiGHS solver benchmarks
.venv/bin/python bench/benchmark_kernel.py
```

## Deployment (Vercel + Backend Container)

1. **Backend (`Dockerfile` / `render.yaml`)**: Deploy the repo to any Docker host (Render Blueprint via `render.yaml`, Railway, Fly.io, or an internal server). The multi-stage `Dockerfile` compiles the Rust PyO3 kernel and runs `uvicorn app.main:app` on `$PORT` with health check `/api/health`.
2. **Frontend (`vercel.json`)**: Connect the repo to Vercel (the root `vercel.json` builds `frontend/` into `frontend/dist` automatically). In Vercel Project Settings → Environment Variables, set `VITE_API_BASE_URL` to your backend URL (for example `https://roaringlion-api.onrender.com`) and redeploy.
