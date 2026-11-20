# Multi-stage build for RoaringLion FastAPI + Rust PyO3/Rayon kernel
FROM python:3.12-slim AS rust-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates pkg-config \
    && rm -rf /var/lib/apt/lists/*

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --default-toolchain stable

RUN pip install --no-cache-dir "maturin>=1.7,<2.0"

WORKDIR /build/engine
COPY engine/Cargo.toml engine/Cargo.lock engine/pyproject.toml ./
COPY engine/src ./src

RUN maturin build --release --out /wheels


FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=rust-builder /wheels/*.whl /tmp/wheels/
COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/app /app/backend/app
COPY bench/results.json /app/bench/results.json

RUN pip install --no-cache-dir /tmp/wheels/*.whl \
    && pip install --no-cache-dir /app/backend \
    && rm -rf /tmp/wheels

ENV PYTHONPATH=/app/backend \
    ROARING_LION_DATA_DIR=/data \
    PORT=8000

RUN mkdir -p /data

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
