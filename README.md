# x402-validator-tools

Complementary tools for **[smartflowproai-lang/x402-endpoint-validator](https://github.com/smartflowproai-lang/x402-endpoint-validator)** — the core conformance engine.

This monorepo bundles three operator-facing surfaces around the engine:

| Component    | Tech           | Purpose                                            |
|--------------|----------------|----------------------------------------------------|
| `api_server` | FastAPI + Stripe | Paid API: `POST /validate`, public demo, accounts |
| `dashboard`  | Flask            | Human UI to browse audit history                  |
| `proxy`      | aiohttp          | Middleware that validates proxied traffic         |

The conformance engine is the separate package **`x402-conformance-suite`**
(declared in `pyproject.toml`).

## Quick start

```bash
git clone https://github.com/MSSATANASS/x402-validator-tools
cd x402-validator-tools
pip install -e ".[dev]"
```

Mint an API key (JSON keystore by default; use `DATABASE_URL` in production):

```bash
export ADMIN_SECRET=dev-admin
# start the API, then:
curl -s -X POST http://127.0.0.1:8000/admin/keys \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"plan_id":"pro"}'
```

Run any of the three components:

```bash
x402-api        # FastAPI on :8000  (OpenAPI at /docs)
x402-dashboard  # Flask on :5000
x402-proxy      # aiohttp on :8080
```

### Request validation (HTTP 422)

Audit and admin JSON bodies are strict Pydantic models (`extra=forbid`):

| Body field | Rule |
|------------|------|
| `url` | Absolute `http`/`https` URL with a host (max 2048 chars) |
| `mode` | `standard` or `marketplace` only |
| `plan_id` | `free` \| `pro` \| `enterprise` |

Invalid payloads return **422** with FastAPI’s `{"detail":[…]}` list (field path + message).  
Unknown `plan_id` on checkout query params also returns **422**.

```bash
# Example: rejected URL scheme
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/audit-public \
  -H "Content-Type: application/json" \
  -d '{"url":"ftp://example.com"}'
# → 422
```

Interactive schema: **`/docs`** (Swagger) and **`/redoc`**.

### Structured logging

The API emits **JSON lines** (JSONL) in production / when `LOG_FORMAT=json`:

| Env | Default | Meaning |
|-----|---------|---------|
| `LOG_FORMAT` | `json` on Render / `ENV=production`; else `text` | Log encoder |
| `LOG_LEVEL` | `INFO` | Logger level |

Each HTTP response includes **`X-Request-Id`** (echoes your header if you send one).  
Access lines use `event=http.access`; audits use `event=audit.completed` / `audit.failed` with `url`, `mode`, `overall`, `latency_ms`, `checks_total`, `checks_failed`.

### Prometheus, rate limits, cache, OpenTelemetry

| Feature | Env | Notes |
|---------|-----|--------|
| **Metrics** | `METRICS_ENABLED=1` (default) | `GET /metrics` — HTTP latency/status, audits PASS/FAIL, rate-limit & cache counters |
| **Per-key rate limit** | `API_KEY_RATE_LIMIT_ENABLED=1` | Hourly burst caps by plan (`RATE_LIMIT_KEY_FREE=30`, `_PRO=120`, `_ENTERPRISE=600`; window `RATE_LIMIT_KEY_WINDOW_SECONDS=3600`) |
| **Audit cache** | `AUDIT_CACHE_TTL_SECONDS=0` (off) | In-process TTL cache by `url\|mode`. Skips AI (`advise`/`explain`) and live `batch-settlement` offers. Header `X-Audit-Cache: HIT\|STORE\|SKIP` |
| **OpenTelemetry** | `OTEL_ENABLED=1` or `OTEL_EXPORTER_OTLP_ENDPOINT` | Optional: `pip install -e ".[otel]"` |

## Docker

```bash
docker compose up --build
```

Multi-stage `Dockerfile`: builder installs the package into `/install`, runtime
copies only site-packages + `api_server` / `dashboard` / `proxy` / `static`.
`.dockerignore` excludes `.venv`, tests, and caches to shrink build context.

```bash
# API only
docker build -t x402-api .
docker run --rm -p 8000:8000 -e COMPONENT=api -e LOG_FORMAT=json x402-api
```

See `docker-compose.yml` for multi-service ports and `DATABASE_URL`.

## Configuration

| Component    | Env vars                                       |
|--------------|------------------------------------------------|
| `api_server` | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `PUBLIC_URL`, `HOST`, `PORT`, `API_KEYS_FILE`, `DATABASE_URL` (optional), `ADMIN_SECRET`, `AUDIT_PUBLIC_DAILY_LIMIT`, `LOG_FORMAT`, `LOG_LEVEL`, `DASHSCOPE_API_KEY` (optional AI advice) |
| `dashboard`  | `FLASK_DEBUG`, `HOST`, `PORT`                  |
| `proxy`      | proxy reads `proxy/config.yaml` at startup     |

### Database backend (PostgreSQL / PolarDB)

By default the API persists keys to a JSON file (`api_keys.json`, override
with `API_KEYS_FILE`). Setting `DATABASE_URL` switches to the
PostgreSQL-backed store (`api_server/dbkeystore.py`) with the same interface
plus three upgrades:

```bash
export DATABASE_URL=postgresql://user:pass@<host>:5432/x402
```

1. **Real monthly quotas** — `/validate` returns 429 once a key exhausts its
   plan (100 / 500 / 5,000 audits per month). The JSON backend historically
   does not enforce quotas.
2. **Audit log** — one row per served audit (timestamp, URL, mode, overall,
   latency, plan tier; public-demo rows carry no identity). Powers the live
   counters on `/open`.
3. **Multi-replica safe** — the database is the lock, so several app
   instances can share one keystore.

Schema is created idempotently at boot. To migrate existing JSON data:

```bash
DATABASE_URL=postgresql://... python scripts/migrate_keystore_to_db.py /var/data/api_keys.json
```

The migration is idempotent (`ON CONFLICT DO NOTHING`) and never prints
credentials. Works with any PostgreSQL 12+, including Alibaba Cloud PolarDB
(always-free 2C8G tier) and the `db` service in `docker-compose.yml`.

Integration tests for the DB backend run when `TEST_DATABASE_URL` is set:

```bash
TEST_DATABASE_URL=postgresql://x402:x402@localhost:5432/x402 \
    pytest tests/test_dbkeystore.py -q
```

## Architecture

```
                          ┌─────────────────────┐
   "validate this URL"    │     api_server      │  ← pays with Stripe
   ─────────────────────► │     (FastAPI)       │
                          └──────────┬──────────┘
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │  x402-validator     │  (pip package, separate repo)
                          │  conformance engine │
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
              ▼                      ▼                      ▼
       ┌────────────┐         ┌────────────┐        ┌────────────┐
       │ dashboard  │         │   proxy    │        │ your code  │
       │  (Flask)   │         │  (aiohttp) │        │ (CLI / CI) │
       └────────────┘         └────────────┘        └────────────┘
```

## Repo layout

```
x402-validator-tools/
├── api_server/             ← FastAPI + Stripe + structured logging
│   ├── app.py
│   ├── models.py           ← strict ValidateRequest / IssueKeyRequest
│   ├── logging_config.py   ← JSONL + X-Request-Id middleware
│   ├── auth.py / dbkeystore.py
│   └── …
├── static/                 ← brand assets (favicon, logos, og-image)
├── dashboard/              ← Flask UI
├── proxy/                  ← aiohttp middleware
├── tests/
├── pyproject.toml
├── Dockerfile              ← multi-stage, non-root
├── .dockerignore
├── docker-compose.yml
└── README.md
```

## Development

```bash
pytest -q
ruff check api_server proxy dashboard
mypy api_server --ignore-missing-imports
```

Coverage: critical modules (`auth`, `dbkeystore`) aim for full hermetic unit coverage;
integration tests for Postgres stay behind `TEST_DATABASE_URL`.

## Deployment

This repo is **not auto-published**. Owner deploys:

- **Render.com** Pro plan for `api_server` and `dashboard` (live at
  `https://x402-validator-tools.onrender.com`).
- The proxy is containerised via the included `Dockerfile` but is
  operator-deployed per-environment.

## License

Apache-2.0.

## Related repos

- [smartflowproai-lang/x402-endpoint-validator](https://github.com/smartflowproai-lang/x402-endpoint-validator) — core conformance engine (upstream)
- [MSSATANASS/x402-conformance-engine](https://github.com/MSSATANASS/x402-conformance-engine) — engine development branch
- [MSSATANASS/x402-endpoint-validator](https://github.com/MSSATANASS/x402-endpoint-validator) — fork used for upstream PRs
