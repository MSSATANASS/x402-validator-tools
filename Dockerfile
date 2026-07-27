# syntax=docker/dockerfile:1

# ----- builder -----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml LICENSE README.md ./
COPY api_server ./api_server
COPY dashboard ./dashboard
COPY proxy ./proxy

RUN pip install --upgrade pip && pip install .

# ----- runtime -----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root user for the runtime image
RUN useradd --create-home --uid 10001 x402

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/x402-* /usr/local/bin/
COPY api_server ./api_server
COPY dashboard ./dashboard
COPY proxy ./proxy
COPY README.md ./

USER x402

# Default to the API server; override per-component at run-time.
ENV COMPONENT=api \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000 5000 8080

CMD ["sh", "-c", "case \"$COMPONENT\" in api) exec x402-api ;; dashboard) exec x402-dashboard ;; proxy) cd /app/proxy && exec x402-proxy ;; *) exec x402-api ;; esac"]
