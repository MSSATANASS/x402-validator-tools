"""Utilities for nonce generation, verification, and EIP-3009 replay protection.

x402 challenges often require nonces to prevent replay attacks. This module provides:
  - Cryptographically secure nonce generation
  - Nonce validation and expiry tracking
  - EIP-3009 style authorization helpers
  - Anchoring with policy_hash and salt
"""

import hashlib
import secrets
import json
import time
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta


@dataclass
class NonceRecord:
    """Record of an issued nonce."""
    nonce: str
    issued_at: float  # Unix timestamp
    expires_at: float  # Unix timestamp
    used: bool = False
    used_at: Optional[float] = None
    challenger_id: Optional[str] = None  # exchange or facilitator ID


class NonceManager:
    """Manage nonce lifecycle: generation, validation, expiry."""

    def __init__(self, nonce_ttl_seconds: int = 300):
        """Initialize nonce manager.

        Args:
            nonce_ttl_seconds: Time-to-live for nonces (default 5 minutes)
        """
        self.nonce_ttl_seconds = nonce_ttl_seconds
        self.nonces: Dict[str, NonceRecord] = {}

    def generate_nonce(self, length: int = 32, challenger_id: Optional[str] = None) -> str:
        """Generate a cryptographically secure random nonce.

        Args:
            length: Number of random bytes (default 32 = 64 hex chars)
            challenger_id: Optional identifier of the issuing exchange/facilitator

        Returns:
            Hex-encoded nonce string
        """
        nonce = secrets.token_hex(length)
        now = time.time()
        self.nonces[nonce] = NonceRecord(
            nonce=nonce,
            issued_at=now,
            expires_at=now + self.nonce_ttl_seconds,
            challenger_id=challenger_id,
        )
        return nonce

    def validate_nonce(self, nonce: str) -> Tuple[bool, str]:
        """Validate a nonce: check existence, expiry, and single-use.

        Returns:
            (is_valid, error_message)
        """
        if nonce not in self.nonces:
            return False, f"nonce not found: {nonce}"

        record = self.nonces[nonce]
        now = time.time()

        if now > record.expires_at:
            return False, f"nonce expired at {datetime.fromtimestamp(record.expires_at)}"

        if record.used:
            return False, f"nonce already used at {datetime.fromtimestamp(record.used_at)}"

        return True, ""

    def mark_nonce_used(self, nonce: str) -> bool:
        """Mark a nonce as consumed (single-use).

        Returns:
            True if marked successfully, False if already used or expired
        """
        is_valid, _ = self.validate_nonce(nonce)
        if not is_valid:
            return False

        record = self.nonces[nonce]
        record.used = True
        record.used_at = time.time()
        return True

    def cleanup_expired(self) -> int:
        """Remove expired nonces from tracking.

        Returns:
            Number of nonces removed
        """
        now = time.time()
        expired = [n for n, r in self.nonces.items() if r.expires_at < now]
        for nonce in expired:
            del self.nonces[nonce]
        return len(expired)


def compute_authorization_hash(
    nonce: str,
    response_body: str,
    policy_hash: Optional[str] = None,
    salt: Optional[str] = None,
) -> str:
    """Compute EIP-3009 style authorization hash.

    Hash combines nonce, response body, optional policy hash and salt to bind
    the authorization to specific conditions.

    Args:
        nonce: Challenge nonce from server
        response_body: Full 402 response body (JSON)
        policy_hash: Optional hash of accepted payment terms/policy
        salt: Optional random salt for additional binding

    Returns:
        Hex-encoded SHA256 hash
    """
    components = [nonce, response_body]

    if policy_hash:
        components.append(policy_hash)
    if salt:
        components.append(salt)

    combined = "||".join(components)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def compute_policy_hash(policy: Dict[str, Any]) -> str:
    """Compute hash of payment policy/terms.

    Ensures deterministic serialization for consistent hashing across parties.

    Args:
        policy: Dictionary of policy terms (maxAmount, TTL, accepted chains, etc.)

    Returns:
        Hex-encoded SHA256 hash
    """
    json_bytes = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(json_bytes).hexdigest()


def generate_anchored_authorization(
    nonce: str,
    response_body: str,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Generate a complete anchored authorization record.

    Combines nonce, policy hash, random salt, and authorization hash for
    binding payment authorization to specific conditions.

    Args:
        nonce: Challenge nonce from server
        response_body: Full 402 response body
        policy: Optional policy terms to anchor

    Returns:
        Dictionary with nonce, policyHash, salt, and authorizationHash
    """
    salt = secrets.token_hex(16)

    policy_hash = compute_policy_hash(policy) if policy else ""
    auth_hash = compute_authorization_hash(nonce, response_body, policy_hash, salt)

    return {
        "nonce": nonce,
        "salt": salt,
        "policyHash": policy_hash,
        "authorizationHash": auth_hash,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


def verify_authorization(
    auth: Dict[str, str],
    response_body: str,
    policy: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Verify an anchored authorization.

    Recomputes authorization hash to ensure consistency.

    Args:
        auth: Authorization record from generate_anchored_authorization
        response_body: Same response body used to generate auth
        policy: Same policy used to generate auth (if any)

    Returns:
        (is_valid, error_message)
    """
    expected_policy_hash = compute_policy_hash(policy) if policy else ""
    if auth.get("policyHash", "") != expected_policy_hash:
        return False, "policy hash mismatch"

    recomputed = compute_authorization_hash(
        auth.get("nonce", ""),
        response_body,
        auth.get("policyHash"),
        auth.get("salt"),
    )

    if recomputed != auth.get("authorizationHash"):
        return False, "authorization hash mismatch"

    return True, ""


def example_usage():
    """Example: generate and verify nonce-based authorization."""
    manager = NonceManager(nonce_ttl_seconds=600)

    # Server issues nonce
    nonce = manager.generate_nonce(challenger_id="binance.com")
    print(f"Generated nonce: {nonce}")

    # Client receives 402, builds authorization
    response_402 = json.dumps({
        "x402Version": "1",
        "accepts": [{"scheme": "eip191", "network": "eip155:1", "asset": "usd", "payTo": "0x..."}],
        "nonce": nonce,
    })

    policy = {
        "maxAmount": "100.00",
        "currency": "usd",
        "ttl": 300,
    }

    auth = generate_anchored_authorization(nonce, response_402, policy)
    print(f"Authorization: {json.dumps(auth, indent=2)}")

    # Server verifies authorization
    valid, err = verify_authorization(auth, response_402, policy)
    print(f"Valid: {valid}, Error: {err}")

    # Mark nonce as used
    manager.mark_nonce_used(nonce)
    print(f"Nonce marked as used")

    # Attempt reuse (should fail)
    valid2, err2 = manager.validate_nonce(nonce)
    print(f"Reuse attempt valid: {valid2}, Error: {err2}")


if __name__ == "__main__":
    example_usage()
