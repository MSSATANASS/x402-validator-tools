# Facilitator metrics — ingest contract

Instrumentation for **facilitator gas cost** and **agent wallet reuse** per
endpoint. Scaffolding only: no rate-limit or compensation logic lives here.

Module: `api_server/facilitator_metrics.py`  
CLIs: `scripts/facilitator_rpc_backfill.py`, `scripts/facilitator_export_report.py`,
`scripts/hash_wallet.py`

## Privacy note (read this)

**`wallet_hash` does not anonymize anyone when the record also includes
`tx_hash`.** Anyone can open the transaction on Basescan (or query Base RPC)
and read the real `from` address. The hash exists to **group and deduplicate**
reuse counts without pasting the address into every CSV cell — not for
privacy or anonymity. Never treat exported reports as anonymized datasets.

Never log or export **private keys**. Prefer `wallet_hash` over raw addresses
in reports; if you must debug, use at most the last 6 hex chars of the
address in local DEBUG logs (not in committed samples).

## Source A — app log / manual JSONL

When settlement code (Fase 4 dogfood or offline protocol tests) submits a
transaction, record one event after obtaining `tx_hash`:

| Field | Required | Notes |
|-------|----------|--------|
| `tx_hash` | yes* | Public. `*` required for chain merge |
| `wallet_hash` | recommended | `sha256("x402-wallet-v1:" + address.lower())` |
| `endpoint` | yes | Canonical resource URL |
| `timestamp` | yes | ISO-8601 UTC |
| `chain_id` | no | Default `8453` (Base) |
| `role` | no | `agent` (default) or `facilitator` |
| `income_usd` | no | Fee received for this call, if known |
| `ingest` | no | `{ "state", "method" }` e.g. `recorded` / `manual_jsonl` |

### Python (Fase 4 hook)

```python
from api_server.facilitator_metrics import record_tx_submitted

event = record_tx_submitted(
    tx_hash=tx_hash,
    wallet_address=agent_address,  # hashed inside; not stored
    endpoint=resource_url,
    chain_id=8453,
    role="agent",
)
# append event.to_dict() as one JSONL line, or store.append(event)
```

### Manual JSONL (today)

Path (local, gitignored): `data/facilitator_events.jsonl`

```json
{"tx_hash":"0x…","wallet_hash":"…","endpoint":"https://…","timestamp":"2026-08-09T18:00:00Z","chain_id":8453,"role":"agent","ingest":{"state":"recorded","method":"manual_jsonl"}}
```

Hash an address without writing it to disk:

```bash
python scripts/hash_wallet.py 0xYourPublicAddress
```

Sample (synthetic, commit-safe): `examples/facilitator_events.sample.jsonl`

## Source B — Base RPC backfill

```bash
python scripts/facilitator_rpc_backfill.py \
  --in data/facilitator_events.jsonl \
  --out data/facilitator_merged.jsonl
```

- Env: `BASE_RPC_URL` (default public `https://mainnet.base.org`)
- Optional USD: `ETH_USD_PRICE` (manual; `price_source=env_manual`)
- Pending / missing txs return `{state, method}` — no hard crash on batch
- **Idempotent:** output deduped by `tx_hash` (last write wins)

## Export report

```bash
python scripts/facilitator_export_report.py \
  --in examples/facilitator_events.sample.jsonl \
  --out reports/facilitator_metrics_sample.json \
  --synthetic --with-sample-gas
```

- Writes JSON + `*_tx.csv` + `*_reuse.csv`
- Reprocessing the same JSONL **does not** duplicate rows or inflate reuse
- `data/` and `reports/*.json|csv` are gitignored; only `examples/` samples commit

## Merge rules

| Field | Winner |
|-------|--------|
| `gas_used`, `gas_price_wei`, `gas_cost_*`, `block_number` | **Chain (B)** |
| `wallet_hash`, `endpoint`, `submitted_at` | **App / manual (A)** |
| Missing wallet on A but RPC `from` present | hash(`from`), method `derived_from_rpc` |

## Reuse `{state, method}`

| state | method | meaning |
|-------|--------|---------|
| `unique` | `first_seen` | 1 hit in window |
| `reused` | `same_key_Nx` | N≥2 in 1h (N=`count_1h`) |
| `reused` | `same_key_24h` | reuse only visible in 24h |
| `unknown` | `no_wallet` | missing wallet_hash |

Primary signal for compensation design: **`count_1h`** (and `same_key_4x`+).

## What this is not

- Not a rate limiter
- Not a compensation mechanism
- Not production facilitator settlement
- Not an anonymization layer
