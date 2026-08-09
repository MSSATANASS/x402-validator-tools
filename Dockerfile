# syntax=docker/dockerfile:1
# Multi-stage: install the package once in builder, copy only site-packages +
# runtime assets. Static files and package sources live under /app so Path-based
# asset resolution (landing HTML, static/*) keeps working.

# ----- builder -----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Dependency metadata first for better layer caching when only source changes.
COPY pyproject.toml LICENSE README.md ./
COPY api_server ./api_server
COPY dashboard ./dashboard
COPY proxy ./proxy

RUN pip install --upgrade pip wheel \
    && pip install --prefix=/install .

# ----- runtime -----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Structured JSON logs on Render by default (see api_server.logging_config).
    LOG_FORMAT=json \
    LOG_LEVEL=INFO \
    COMPONENT=api \
    HOST=0.0.0.0 \
    PORT=8000

# Non-root user
RUN useradd --create-home --uid 10001 x402

WORKDIR /app

# Installed package + console scripts only (no build toolchain).
COPY --from=builder /install /usr/local

# Runtime assets: package sources (HTML/CSS embedded) + static brand files.
COPY --chown=x402:x402 api_server ./api_server
COPY --chown=x402:x402 dashboard ./dashboard
COPY --chown=x402:x402 proxy ./proxy
COPY --chown=x402:x402 static ./static
COPY --chown=x402:x402 README.md ./

USER x402

EXPOSE 8000 5000 8080

# Prefer exec form via shell only for COMPONENT switch.
CMD ["sh", "-c", "case \"$COMPONENT\" in api) exec x402-api ;; dashboard) exec x402-dashboard ;; proxy) cd /app/proxy && exec x402-proxy ;; *) exec x402-api ;; esac"]
