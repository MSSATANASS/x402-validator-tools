"""Tests for all top-10 x402 exchange problem solutions."""

import pytest
import json
from datetime import datetime, timedelta

# Import all solution modules
from examples.cold_probe_client import ColdProbeClient, ProbeConfig, BackoffStrategy
from examples.nonce_helpers import NonceManager, generate_anchored_authorization, verify_authorization
from examples.facilitator_classifier import assess_facilitator, FacilitatorType, RiskLevel
from examples.manifest_linter import ManifestLinter
from examples.key_rotation import KeyStore
from examples.receipt_utils import parse_receipt, compute_response_hash, verify_binding


class TestColdProbeRobustness:
    """Test #1: Cold-probe robustness with retry/backoff."""

    def test_backoff_calculation_exponential(self):
        config = ProbeConfig(
            base_url="https://test.com",
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
            initial_backoff_seconds=1.0,
            max_backoff_seconds=60.0,
        )
        # Exponential backoff: 1, 2, 4, 8, 16, 32, 60 (capped)
        # Just verify it increases
        from examples.cold_probe_client import calculate_backoff
        b0 = calculate_backoff(0, config)
        b1 = calculate_backoff(1, config)
        assert b1 > b0, "exponential backoff should increase"

    def test_backoff_calculation_linear(self):
        config = ProbeConfig(
            base_url="https://test.com",
            backoff_strategy=BackoffStrategy.LINEAR,
            initial_backoff_seconds=1.0,
            jitter_factor=0.0,  # Disable jitter for deterministic test
        )
        from examples.cold_probe_client import calculate_backoff
        b0 = calculate_backoff(0, config)
        b1 = calculate_backoff(1, config)
        # Without jitter, b1 should be b0 + 1.0 (linear increase)
        assert abs(b1 - b0 - 1.0) < 0.01, "linear backoff should increase by initial_backoff"

    def test_realistic_headers(self):
        from examples.cold_probe_client import get_realistic_headers
        headers = get_realistic_headers()
        assert "User-Agent" in headers
        assert "Mozilla" in headers["User-Agent"]
        assert headers["DNT"] == "1"


class TestManifestExposure:
    """Test #2: Manifest linting and CDN-friendly exposure."""

    def test_linter_missing_version(self):
        linter = ManifestLinter()
        manifest = {"accepts": []}
        errors = linter.lint(manifest)
        assert any(e.field == "x402Version" and e.level == "error" for e in errors)

    def test_linter_invalid_caip2(self):
        linter = ManifestLinter()
        manifest = {
            "x402Version": "1",
            "accepts": [
                {
                    "scheme": "eip191",
                    "network": "not_valid_caip2",  # Invalid
                    "asset": "usd",
                    "payTo": "0x...",
                }
            ],
        }
        errors = linter.lint(manifest)
        assert any(e.field == "accepts[0].network" for e in errors)

    def test_linter_valid_manifest(self):
        linter = ManifestLinter()
        manifest = {
            "x402Version": "1",
            "accepts": [
                {
                    "scheme": "eip191",
                    "network": "eip155:1",
                    "asset": "usd",
                    "payTo": "0x...",
                }
            ],
            "resource": "https://api.example.com",
        }
        errors = linter.lint(manifest)
        assert not any(e.level == "error" for e in errors)


class TestCAIP2Validation:
    """Test #3: CAIP-2 validation and naming standards."""

    def test_valid_caip2_formats(self):
        from examples.x402_validators import is_valid_caip2
        assert is_valid_caip2("eip155:1")
        assert is_valid_caip2("solana:5eykt4UsFv2P6zt3S6i38hWrwLcPrqq3")
        assert is_valid_caip2("bitcoin:mainnet")

    def test_invalid_caip2_formats(self):
        from examples.x402_validators import is_valid_caip2
        assert not is_valid_caip2("not_caip2")
        assert not is_valid_caip2("eip155")  # Missing reference
        assert not is_valid_caip2("")


class TestReceiptVerification:
    """Test #4: Receipt verification with crypto algorithms."""

    def test_parse_receipt_valid(self):
        receipt = {
            "receiptVersion": "1",
            "responseHash": "abc123",
            "signature": "def456",
            "signer": "0xdead",
            "algorithm": "placeholder",
        }
        parsed = parse_receipt(receipt)
        assert parsed["receiptVersion"] == "1"

    def test_receipt_binding_verification(self):
        body = '{"data": "test"}'
        resp_hash = compute_response_hash(body)
        receipt = {
            "receiptVersion": "1",
            "responseHash": resp_hash,
            "signature": resp_hash,
            "signer": "0xdead",
            "algorithm": "placeholder",
        }
        assert verify_binding(receipt, body)

    def test_receipt_binding_mismatch(self):
        receipt = {
            "receiptVersion": "1",
            "responseHash": "abc123",
            "signature": "def456",
            "signer": "0xdead",
            "algorithm": "placeholder",
        }
        assert not verify_binding(receipt, "different body")


class TestNonceAndAuthorization:
    """Test #5: Nonce generation, verification, and EIP-3009 helpers."""

    def test_nonce_generation(self):
        manager = NonceManager(nonce_ttl_seconds=300)
        nonce = manager.generate_nonce(length=32)
        assert len(nonce) == 64  # 32 bytes = 64 hex chars
        assert nonce.isalnum()

    def test_nonce_validation(self):
        manager = NonceManager(nonce_ttl_seconds=300)
        nonce = manager.generate_nonce()
        valid, err = manager.validate_nonce(nonce)
        assert valid

    def test_nonce_single_use(self):
        manager = NonceManager(nonce_ttl_seconds=300)
        nonce = manager.generate_nonce()
        manager.mark_nonce_used(nonce)
        valid, err = manager.validate_nonce(nonce)
        assert not valid
        assert "already used" in err

    def test_anchored_authorization(self):
        response_body = '{"x402Version": "1", "accepts": []}'
        nonce = "test_nonce"
        policy = {"maxAmount": "100.00"}
        auth = generate_anchored_authorization(nonce, response_body, policy)
        assert "authorizationHash" in auth
        assert auth["nonce"] == nonce

    def test_authorization_verification(self):
        response_body = '{"x402Version": "1"}'
        nonce = "test_nonce"
        policy = {"maxAmount": "100.00"}
        auth = generate_anchored_authorization(nonce, response_body, policy)
        valid, err = verify_authorization(auth, response_body, policy)
        assert valid, err


class TestFacilitatorClassification:
    """Test #6: Facilitator classification and wash-trade detection."""

    def test_assess_independent_facilitator(self):
        exchanges = {"binance": {"name": "Binance", "affiliates": []}}
        fac = {
            "id": "indep_1",
            "name": "Independent",
            "settlement_volume_usd": 100000.0,
            "onchain_volume_usd": 95000.0,  # Good correlation
            "recent_settlement_times_ms": [3000, 4000],  # Normal speed
            "settlement_method": "on-chain",
            "key_disclosure_status": "verified",
        }
        profile = assess_facilitator(fac, exchanges)
        assert profile.facilitator_type == FacilitatorType.INDEPENDENT_THIRD_PARTY
        assert profile.risk_level == RiskLevel.LOW

    def test_assess_exchange_operated_facilitator(self):
        exchanges = {"binance": {"name": "Binance", "affiliates": []}}
        fac = {
            "id": "binance_fac",
            "name": "Binance Settlement",
            "parent": "binance",
            "settlement_volume_usd": 1000000.0,
            "onchain_volume_usd": 50000.0,  # Low correlation - wash risk!
            "recent_settlement_times_ms": [100, 150],  # Very fast
            "settlement_method": "custodial",
            "key_disclosure_status": "none",
        }
        profile = assess_facilitator(fac, exchanges)
        assert profile.facilitator_type == FacilitatorType.EXCHANGE_OPERATED
        assert profile.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        assert "wash_suspected" in profile.historical_flags


class TestKeyRotation:
    """Test #9: Key rotation and revocation management."""

    def test_add_key(self):
        store = KeyStore()
        key = store.add_key("key_1", "ed25519", "abc" * 11)
        assert key.state == "valid"
        assert key.key_id == "key_1"

    def test_rotate_key(self):
        store = KeyStore()
        store.add_key("key_1", "ed25519", "abc" * 11)
        store.add_key("key_2", "ed25519", "def" * 11)
        success, msg = store.rotate_key("key_1", "key_2")
        assert success

    def test_revoke_key(self):
        store = KeyStore()
        store.add_key("key_1", "ed25519", "abc" * 11)
        success, msg = store.revoke_key("key_1", "compromise")
        assert success
        key = store.get_key_for_verification("key_1")
        assert key is None  # Revoked keys not usable

    def test_historical_key_lookup(self):
        store = KeyStore()
        # Create key on 2024-01-01
        now = datetime.utcnow()
        past = (now - timedelta(days=30)).isoformat() + "Z"

        key = store.add_key("key_1", "ed25519", "abc" * 11)
        # Manually set created_at to past
        key.created_at = past

        # Lookup at that time should find the key
        future = (now + timedelta(days=1)).isoformat() + "Z"
        result = store.get_key_for_verification("key_1", at_time=future)
        assert result is not None


def test_integration_full_workflow():
    """Integration test: cold-probe → manifest linting → verification."""
    # Simulate an exchange manifest discovery workflow

    # 1. Cold-probe discovers manifest
    config = ProbeConfig(base_url="https://api.example.com", max_retries=1)
    # (We won't actually probe, just test config)
    assert config.base_url == "https://api.example.com"

    # 2. Lint manifest
    manifest = {
        "x402Version": "1",
        "accepts": [
            {
                "scheme": "eip191",
                "network": "eip155:1",
                "asset": "usd",
                "payTo": "0x...",
            }
        ],
        "resource": "https://api.example.com/api/v1",
    }
    linter = ManifestLinter()
    errors = linter.lint(manifest)
    assert not any(e.level == "error" for e in errors)

    # 3. Generate nonce-based challenge
    manager = NonceManager()
    nonce = manager.generate_nonce(challenger_id="example")
    manifest["nonce"] = nonce

    # 4. Client builds authorization
    manifest_json = json.dumps(manifest)
    auth = generate_anchored_authorization(nonce, manifest_json)

    # 5. Server verifies authorization
    valid, _ = verify_authorization(auth, manifest_json)
    assert valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
