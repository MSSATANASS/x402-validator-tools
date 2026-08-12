PR: Add x402 validators, receipt verification, facilitator detector, and CI

Summary
This PR introduces a suite of tools and tests to make this repository a reference validator for x402 endpoints, with a focus on interoperability and real-world issues observed at large exchanges (Binance, Coinbase, Kraken, OKX, Huobi).

What changed
- docs/x402-top-10.md — analysis of top-10 problems with exchanges and recommended fixes (Hecho por mss_ali)
- examples/x402_validators.py — CAIP-2, manifest shape validator, 402-challenge parser
- examples/receipt_utils.py — full receipt parsing and verification (ed25519 + ecdsa-secp256k1), plus backward-compatible placeholder
- examples/facilitator_detector.py — simple wash/self-routing heuristics
- .github/workflows/x402-validators.yml — CI workflow: install dev deps and run tests/validators
- tests/* — unit tests for new utilities
- pyproject.toml — added cryptography, pynacl dependencies

Tests
All tests pass locally: 294 passed, 43 skipped, 1 warning.

Notes for reviewers
- Signature verification supports ed25519 (signer is hex public key) and ecdsa-secp256k1 (signer is PEM public key). Adjust signer format to match your operational key publication method.
- A placeholder verify_signature_placeholder is retained for legacy/quick-check receipts.
- This change does not push any secrets; keys in tests are ephemeral.

How to apply locally
1) Apply patch produced by the agent: git apply x402-complete.patch
2) Create venv and install dev deps:
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
   .\.venv\Scripts\python.exe -m pip install -e .[dev]
3) Run tests: .\.venv\Scripts\python.exe -m pytest -q

Suggested reviewers: API/auth, security/crypto, infra/CDN/WAF

Release notes (short)
- Add x402 conformance validators and CI; implement receipt verification (ed25519/ECDSA) and facilitator heuristics. Marked as authored by mss_ali.