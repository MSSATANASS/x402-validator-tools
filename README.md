# x402-validator-tools

Complementary tools for **[smartflowproai-lang/x402-endpoint-validator](https://github.com/smartflowproai-lang/x402-endpoint-validator)** — the core conformance engine.

This monorepo bundles three operator-facing surfaces around the engine:

| Component    | Tech           | Purpose                                            |
|--------------|----------------|----------------------------------------------------|
| `api_server` | FastAPI + Stripe | Paid API exposing `POST /validate` per plan        |
| `dashboard`  | Flask          | Human UI to browse audit history                  |
| `proxy`      | aiohttp         | Middleware that validates proxied traffic         |

The core engine lives separately; install it as a dependency:

```bash
pip install x402-validator>=0.3.0
```

## Quick start

```bash
git clone https://github.com/MSSATANASS/x402-validator-tools
cd x402-validator-tools
pip install -e ".[dev]"
```

Provision an API key (in-process; swap for a DB in production):

```python
from api_server.app import api_keys
api_keys["my-key"] = "pro"
```

Run any of the three components:

```bash
x402-api        # FastAPI on :8000
x402-dashboard  # Flask on :5000
x402-proxy      # aiohttp on :8080
```

## Docker

```bash
docker compose up
```

Composes all three components on a single network. See `docker-compose.yml`
for ports, env vars, and volume mounts.

## Configuration

| Component    | Env vars                                       |
|--------------|------------------------------------------------|
| `api_server` | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `PUBLIC_URL`, `HOST`, `PORT`, `API_KEYS_FILE`, `DATABASE_URL` (optional), `ADMIN_SECRET`, `AUDIT_PUBLIC_DAILY_LIMIT` |
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
├── api_server/             ← FastAPI + Stripe
│   ├── app.py
│   ├── models.py
│   └── stripe_integration.py
├── dashboard/              ← Flask UI
│   ├── app.py
│   ├── templates/
│   └── static/
├── proxy/                  ← aiohttp middleware
│   ├── middleware.py
│   └── config.yaml.example
├── tests/
├── pyproject.toml          ← monorepo, installs all three
├── Dockerfile
├── docker-compose.yml
├── .github/workflows/      ← test + docker
├── README.md
└── LICENSE
```

## Development

```bash
pytest --cov=. --cov-report=term-missing
```

Coverage target: ≥80 % on each package.

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
