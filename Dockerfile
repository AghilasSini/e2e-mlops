# syntax=docker/dockerfile:1
# Multi-stage build for the runtime images:
#   --target trainer         runs `train-and-promote` (train, register, promote, resolve)
#   --target inference       serves the FastAPI app on :8080
#   --target mlflow-server   MLflow tracking server + registry (and the bootstrap hook)
#   --target feature-logger  stores KServe's inference CloudEvents in PostgreSQL, :8080
# All run as UID 10001 and contain only a prebuilt virtualenv (plus configuration).
# Base image pinned to a patch release; bump both FROM lines together.


FROM ghcr.io/astral-sh/uv:0.10.9 AS uv

# ---------------------------------------------------------------- builder
FROM python:3.11.15-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
# No precompiled bytecode: saves ~90 MB per image; Python compiles in memory at start-up
# (the root filesystem is read-only, so nothing is written).
ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./

FROM builder AS build-trainer
RUN uv sync --frozen --no-install-project --no-editable --extra train
COPY src ./src
RUN uv sync --frozen --no-editable --extra train \
    # third-party test suites are never imported at runtime (~160 MB)
    && find /opt/venv -depth -type d -name tests -exec rm -rf {} +

FROM builder AS build-inference
RUN uv sync --frozen --no-install-project --no-editable --extra serve
COPY src ./src
RUN uv sync --frozen --no-editable --extra serve \
    # third-party test suites are never imported at runtime (~160 MB)
    && find /opt/venv -depth -type d -name tests -exec rm -rf {} +

FROM builder AS build-feature-logger
# Only the feature-log extra: no numpy/pandas/scikit-learn (the `core` extra).
RUN uv sync --frozen --no-install-project --no-editable --extra feature-log
COPY src ./src
RUN uv sync --frozen --no-editable --extra feature-log \
    && find /opt/venv -depth -type d -name tests -exec rm -rf {} +

FROM builder AS build-mlflow-server
# The server does not need the project itself, only its pinned dependencies.
RUN uv sync --frozen --no-install-project --no-editable --extra tracking-server \
    && find /opt/venv -depth -type d -name tests -exec rm -rf {} +

# ---------------------------------------------------------------- runtime base
FROM python:3.11.15-slim-bookworm AS runtime
ARG GIT_SHA=unknown
ARG IMAGE_TAG=local
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ROOT=/app \
    GIT_SHA=${GIT_SHA} \
    IMAGE_TAG=${IMAGE_TAG}
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY config/config.yaml ./config/config.yaml
COPY params.yaml schema.yaml ./

# ---------------------------------------------------------------- trainer
FROM runtime AS trainer
COPY --from=build-trainer /opt/venv /opt/venv
# Seed dataset (100 kB, versioned with the code). Override with DATA_PATH to train on
# a mounted dataset; Epic 4 replaces this with an extraction step.
COPY data/raw/winequality-red.csv ./data/raw/winequality-red.csv
# Train, register in MLflow, then decide promotion (needs MLFLOW_TRACKING_URI).
COPY --chmod=0755 scripts/train-and-promote.sh /usr/local/bin/train-and-promote
ENV OUTPUT_DIR=/outputs
USER 10001:10001
CMD ["train-and-promote"]

# ---------------------------------------------------------------- inference
FROM runtime AS inference
COPY --from=build-inference /opt/venv /opt/venv
ENV MODEL_URI=models:/wine-quality@champion
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2).status != 200)"]
CMD ["uvicorn", "serving.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

# ---------------------------------------------------------------- mlflow-server
# MLflow tracking server + registry (basic-auth, PostgreSQL, proxied S3 artifacts).
# All connection settings and credentials come from the environment at run time.
FROM python:3.11.15-slim-bookworm AS mlflow-server
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MLFLOW_DISABLE_AGENT_HINT=1
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=build-mlflow-server /opt/venv /opt/venv
COPY --chmod=0755 scripts/mlflow-server.sh /usr/local/bin/mlflow-server
COPY --chmod=0644 scripts/mlflow_bootstrap.py /opt/mlflow/mlflow_bootstrap.py
COPY --chmod=0644 scripts/mlflow_server_exec.py /opt/mlflow/mlflow_server_exec.py
USER 10001:10001
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=2).status != 200)"]
ENTRYPOINT ["mlflow-server"]

# ---------------------------------------------------------------- feature-logger
# Receives the CloudEvents of KServe's logger sidecar and stores them in the feature-log
# database (FEATURE_LOG_DATABASE_URL + FEATURE_LOG_DB_PASSWORD, set at run time).
FROM python:3.11.15-slim-bookworm AS feature-logger
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=build-feature-logger /opt/venv /opt/venv
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2).status != 200)"]
CMD ["uvicorn", "--factory", "feature_log.app:create_app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
