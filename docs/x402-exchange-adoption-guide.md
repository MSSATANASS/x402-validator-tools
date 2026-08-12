# x402 Exchange Adoption Guide — Top-10 Solutions

## Overview

This guide addresses the **10 most common x402 implementation issues** observed in large exchanges (Binance, Coinbase, Kraken, OKX, Huobi) and provides **production-ready solutions** from this repository.

---

## Problem #1: Cold-Probe Blocked by Gateways/Anti-Bot

**Symptom**: Discovery requests to `/.well-known/x402` return 403, 429, 302 instead of 200 or 402.

### Solution

Use the **`ColdProbeClient`** with intelligent retry/backoff:

```python
from examples.cold_probe_client import ColdProbeClient, ProbeConfig, BackoffStrategy

config = ProbeConfig(
    base_url="https://api.binance.com",
    max_retries=5,
    backoff_strategy=BackoffStrategy.EXPONENTIAL,
    initial_backoff_seconds=1.0,
)
client = ColdProbeClient(config)

# Discover manifest with automatic retry/backoff
status, manifest = client.discover_manifest()
if manifest:
    print(f"Found manifest: x402Version {manifest['x402Version']}")
```

### WAF Configuration (Recommended for Exchanges)

Add these rules to your CDN/WAF to allow cold-probes:

```
# Allow well-known discovery from any source (or trusted directories)
Path: /.well-known/x402
Methods: GET, HEAD, OPTIONS
Rate Limit: 100 req/min per IP (generous for probing)
Authentication: NOT required
Bypass Rules: None for this endpoint
```

**Client headers** used for realistic fingerprinting:
- `User-Agent`: Rotated browser strings
- `Accept`: `application/json, text/plain, */*`
- `DNT`: `1` (Do Not Track)
- `Sec-Fetch-*`: Full fetch metadata headers

---

## Problem #2: Manifest Poorly Exposed or Behind ACLs

**Symptom**: Manifest not discoverable, located at wrong path, requires auth.

### Solution

Use the **`ManifestLinter`** to validate and fix manifests:

```python
from examples.manifest_linter import ManifestLinter

manifest = {
    "x402Version": "1",
    "accepts": [...],
    "resource": "https://api.example.com",  # Describe protected resource
}

linter = ManifestLinter()
errors = linter.lint(manifest)
print(linter.format_report())
```

### Best Practices for CDN/Public Exposure

1. **Location**: Always expose at `/.well-known/x402` (RFC 8615 compliant)
2. **Authentication**: No auth required; should be public
3. **Caching**: Use `Cache-Control: public, max-age=3600` (1 hour)
4. **CORS**: Allow cross-origin requests:
   ```
   Access-Control-Allow-Origin: *
   Access-Control-Allow-Methods: GET, HEAD, OPTIONS
   ```
5. **Size**: Keep manifest < 100 KB for CDN compatibility
6. **Format**: Ensure valid JSON with max nesting depth ≤ 5

---

## Problem #3: Invalid or Proprietary CAIP-2 Naming

**Symptom**: Manifest uses custom asset/network names, breaking interoperability.

### Solution

Use **CAIP-2 validation** and standard naming:

```python
from examples.x402_validators import is_valid_caip2, validate_manifest_shape

# Validate network is CAIP-2
assert is_valid_caip2("eip155:1")  # Ethereum mainnet ✓
assert is_valid_caip2("solana:5eykt4UsFv2P6zt3S6i38hWrwLcPrqq3")  # Solana ✓

# Validate manifest structure
errors = validate_manifest_shape({
    "x402Version": "1",
    "accepts": [
        {"scheme": "eip191", "network": "eip155:1", "asset": "usd", "payTo": "0x..."}
    ]
})
assert not errors  # Should be clean
```

### Standard Asset Codes

- **Fiat**: `usd`, `eur`, `gbp`, `jpy`, `aud`, `cad`
- **Crypto**: `btc`, `eth`, `usdc`, `usdt`, `dai`
- **Chains** (use CAIP-2): `eip155:1`, `solana:mainnet`, `bitcoin:mainnet`

---

## Problem #4: Receipts Signed with Unexplained Keys

**Symptom**: Receipts fail verification; signer key not documented.

### Solution

Use the **`receipt_utils`** library with full crypto support:

```python
from examples.receipt_utils import parse_receipt, verify_receipt, verify_binding

receipt = {
    "receiptVersion": "1",
    "responseHash": "sha256_hash_of_response",
    "signature": "ed25519_or_ecdsa_signature_hex",
    "signer": "hex_or_pem_public_key",
    "algorithm": "ed25519"  # or "ecdsa-secp256k1"
}

# Verify binding (responseHash matches actual response)
if verify_binding(receipt, response_body):
    # Verify cryptographic signature
    if verify_receipt(receipt, response_body):
        print("Receipt verified ✓")
```

### Key Disclosure Policy

Add to your manifest or separate `.well-known/x402-keys` endpoint:

```json
{
  "keys": [
    {
      "keyId": "signer_2024_01",
      "algorithm": "ed25519",
      "publicKey": "hex_or_pem",
      "validFrom": "2024-01-01T00:00:00Z",
      "validUntil": "2025-01-01T00:00:00Z"
    }
  ]
}
```

---

## Problem #5: Nonce Reuse and Replay Risks

**Symptom**: Nonces reused, clients can replay old payments, settlement mismatches.

### Solution

Use **`nonce_helpers`** for generation and EIP-3009 anchoring:

```python
from examples.nonce_helpers import NonceManager, generate_anchored_authorization

manager = NonceManager(nonce_ttl_seconds=600)
nonce = manager.generate_nonce(challenger_id="binance.com")

# Client builds anchored authorization
policy = {
    "maxAmount": "1000.00",
    "currency": "usd",
    "ttl": 600,
    "acceptedChains": ["eip155:1"],
}
auth = generate_anchored_authorization(
    nonce, response_body, policy
)

# Server verifies and marks nonce as used
valid, err = verify_authorization(auth, response_body, policy)
if valid:
    manager.mark_nonce_used(nonce)  # Single-use
    process_payment(auth)
```

### Conciliation Off-Chain

For settlements that don't post on-chain immediately:

1. **Hash Anchoring**: Include `policyHash` (hash of terms) in authorization
2. **Salt**: Add random salt to prevent rainbow table attacks
3. **Audit Trail**: Log every nonce generation, use, and validation
4. **Settlement Timeout**: Fail if settlement not confirmed within TTL

---

## Problem #6: Facilitator Wash Trades

**Symptom**: Facilitators are exchange-operated; high settlement volume but minimal on-chain verification.

### Solution

Use **`facilitator_classifier`** to detect wash trades:

```python
from examples.facilitator_classifier import assess_facilitator, RiskLevel

exchanges = {
    "binance": {"name": "Binance", "affiliates": ["binance_us", "binance_jex"]},
    "coinbase": {"name": "Coinbase", "affiliates": ["coinbase_prime"]},
}

facilitator = {
    "id": "settlement_fac_1",
    "name": "Settlement Partner",
    "parent": "binance",  # Ownership indicator
    "settlement_volume_usd": 1000000.0,
    "onchain_volume_usd": 50000.0,  # Mismatch = wash risk!
    "recent_settlement_times_ms": [100, 150],  # Suspiciously fast
    "settlement_method": "custodial",
    "key_disclosure_status": "none",
}

profile = assess_facilitator(facilitator, exchanges)
if profile.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
    print(f"⚠️  Wash trade risk: {profile.historical_flags}")
```

### Mitigation

1. **Volume Correlation**: Require on-chain volume ≥ 80% of settlement volume
2. **Latency Thresholds**: Flag settlement < 1 second as suspicious
3. **Key Disclosure**: Require ed25519 or ECDSA public keys for verification
4. **Ownership Transparency**: Document facilitator → exchange relationships

---

## Problem #7: Pricing/Quote Volatility

**Symptom**: Quotes expire too fast, values inconsistent between manifest and quotes, payment failures.

### Solution

Implement quote TTL standards and test harness:

```python
# In manifest, document quote behavior
{
    "x402Version": "1",
    "quoteCache": {
        "ttlSeconds": 60,
        "refreshStrategy": "on_expiry",
        "staleTolerance": "3600"  # Accept 1-hour-old quotes as fallback
    },
    "accepts": [...]
}

# Test latency simulation
import time
quote_timestamp = time.time()
quote_ttl = 60
settlement_time = time.time() + 45  # Settle 45s later

if settlement_time - quote_timestamp > quote_ttl:
    print("Quote expired during payment attempt")
else:
    print("Quote valid at settlement time")
```

### Recommended TTLs

- **Quote Discovery** (coldprobe → manifest): 300s (5 min)
- **Quote/Challenge**: 60–120s (1–2 min) — short for high-frequency exchanges
- **Authorization Validity**: 600s (10 min) — allows processing time
- **Receipt Freshness**: Unlimited (but track by timestamp)

---

## Problem #8: KYC/Auth Gate Before 402

**Symptom**: Exchange requires login before showing x402 challenge; breaks discovery.

### Solution

Separate KYC/auth from x402 challenge exposure:

```python
# Middleware shim (e.g., FastAPI or Flask)

@app.get("/.well-known/x402")
async def manifest_public():
    """Public endpoint — NO authentication required."""
    return {
        "x402Version": "1",
        "accepts": [...],
        "resource": "Protected API endpoints",
    }

@app.get("/api/v3/protected-endpoint", dependencies=[Depends(verify_auth)])
async def protected(request: Request):
    """Protected endpoint — authentication required."""
    # On first request (no payment), return 402
    if not request.headers.get("x-payment-receipt"):
        return JSONResponse(
            status_code=402,
            content={"x402Version": "1", "accepts": [...], "nonce": "..."},
            headers={"WWW-Authenticate": 'x402 version="1"'},
        )
    # Verify receipt, then serve protected resource
    ...
```

### Policy (Recommended)

- Manifest discovery: **Public** (no auth)
- Cold-probe: **Public** (allows probing before auth)
- Protected resource (data/API): **Requires auth + payment receipt**

---

## Problem #9: Key Rotation and Revocation

**Symptom**: Key rotations break historical receipt verification; no revocation mechanism.

### Solution

Use **`key_rotation`** for versioned key management:

```python
from examples.key_rotation import KeyStore

store = KeyStore()

# Create initial key (valid for 1 year)
key_v1 = store.add_key(
    "signer_2024_01",
    "ed25519",
    "abc123...",  # 32-byte ed25519 public key
    valid_until="2025-01-01T00:00:00Z"
)

# Later: create replacement key
key_v2 = store.add_key(
    "signer_2025_01",
    "ed25519",
    "def456...",
    valid_until="2026-01-01T00:00:00Z"
)

# Rotate: mark v1 as superseded, link to v2
success, msg = store.rotate_key("signer_2024_01", "signer_2025_01")

# Verify historical receipt signed with v1 key
receipt_timestamp = "2024-06-15T10:00:00Z"  # When receipt was issued
key = store.get_key_for_verification("signer_2024_01", at_time=receipt_timestamp)
# key will be valid v1 (rotated but still verifiable at that time)
```

### Revocation States

| State | Meaning | Usable For Verification? |
|-------|---------|-------------------------|
| `valid` | Current, in-use key | ✓ Yes |
| `rotated` | Superseded but not compromised | ✓ Yes (historical only) |
| `revoked` | Compromised or invalid | ✗ No |

---

## Problem #10: No Reproducible CI Tests Against Exchange Sandboxes

**Symptom**: Integrators lack automated validation; no conformance testing in CI.

### Solution

Use the **GitHub Action** for automated testing:

```yaml
# .github/workflows/x402-validator.yml
name: x402 Validator
on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        exchange: [binance.com, api.coinbase.com, kraken.com, okx.com]
    steps:
      - uses: actions/checkout@v4
      - name: Probe ${{ matrix.exchange }}
        run: |
          python -m pytest tests/test_top10_solutions.py -v
```

### Matri

x Coverage

- **Manifest Linting**: Validates all `accepts` options, CAIP-2 compliance, size limits
- **Cold-Probe**: Tests discovery against public sandboxes
- **Receipt Verification**: Unit tests for ed25519 and ECDSA
- **Facilitator Classification**: Detects wash-trade patterns
- **Nonce & Authorization**: Verifies EIP-3009 anchoring
- **Key Rotation**: Tests historical verification

### Running Tests Locally

```bash
# Install dev dependencies
pip install -e .[dev]

# Run all tests
pytest tests/test_top10_solutions.py -v

# Run specific test
pytest tests/test_top10_solutions.py::TestColdProbeRobustness -v
```

---

## Adoption Checklist for Exchanges

- [ ] **Manifest**: Exposed at `/.well-known/x402`, valid JSON, CAIP-2 compliant
- [ ] **Cold-Probe**: Allow unauthenticated discovery (no WAF blocks)
- [ ] **Receipts**: Sign with ed25519 or ECDSA; document key material
- [ ] **Nonce**: Implement single-use nonce validation and TTL
- [ ] **Facilitators**: Disclose ownership, track settlement vs. on-chain volumes
- [ ] **Quotes**: Document TTL and staleness policy
- [ ] **KYC/Auth**: Separate from x402 challenge exposure
- [ ] **Key Rotation**: Maintain version history; support historical verification
- [ ] **CI/Testing**: Run conformance tests on every PR and deployment
- [ ] **Documentation**: Publish x402 adoption guide for API users

---

## Support & Resources

- **Repository**: https://github.com/MSSATANASS/x402-validator-tools
- **CAIP-2 Spec**: https://github.com/chainagnostic/CAIPs/blob/master/CAIPs/caip-2.md
- **x402 Spec**: HTTP 402 Payment Required (RFC)
- **EIP-3009**: Typed structured data hashing (ethereum.org)

