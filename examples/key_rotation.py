"""Key rotation and revocation state management for x402 signers.

Handles key versioning, revocation tracking, and temporal verification
to support key rotation without invalidating historical receipts.

States:
  - valid: Key is currently usable for verification
  - rotated: Key has been superseded by a new version
  - revoked: Key has been explicitly invalidated
"""

import json
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import hashlib


@dataclass
class KeyVersion:
    """A single version of a signing key."""
    key_id: str  # Unique identifier (e.g., "ed25519_2024_01")
    algorithm: str  # "ed25519", "ecdsa-secp256k1", etc.
    public_key: str  # Hex (ed25519) or PEM (ECDSA)
    state: str  # "valid", "rotated", "revoked"
    created_at: str  # ISO 8601 timestamp
    rotated_at: Optional[str]  # ISO 8601 timestamp when superseded
    revoked_at: Optional[str]  # ISO 8601 timestamp when revoked
    revocation_reason: Optional[str]  # Reason for revocation if applicable
    successor_key_id: Optional[str]  # ID of the new key if rotated
    valid_until: Optional[str]  # Optional expiry date


class KeyStore:
    """Manages a set of signing keys and their versions."""

    def __init__(self):
        self.keys: Dict[str, List[KeyVersion]] = {}  # key_id -> [versions]
        self.history: List[Dict[str, Any]] = []  # Audit log

    def add_key(
        self,
        key_id: str,
        algorithm: str,
        public_key: str,
        valid_until: Optional[str] = None,
    ) -> KeyVersion:
        """Add a new key to the keystore.

        Args:
            key_id: Unique key identifier
            algorithm: Signing algorithm
            public_key: Public key material
            valid_until: Optional expiry date (ISO 8601)

        Returns:
            KeyVersion record
        """
        now = datetime.utcnow().isoformat() + "Z"
        version = KeyVersion(
            key_id=key_id,
            algorithm=algorithm,
            public_key=public_key,
            state="valid",
            created_at=now,
            rotated_at=None,
            revoked_at=None,
            revocation_reason=None,
            successor_key_id=None,
            valid_until=valid_until,
        )

        if key_id not in self.keys:
            self.keys[key_id] = []

        self.keys[key_id].append(version)
        self._log("add_key", {"key_id": key_id, "algorithm": algorithm})
        return version

    def rotate_key(self, old_key_id: str, new_key_id: str, reason: str = "routine") -> Tuple[bool, str]:
        """Rotate a key by marking old as rotated and linking to new.

        Args:
            old_key_id: ID of key being retired
            new_key_id: ID of replacement key (must already exist)
            reason: Reason for rotation

        Returns:
            (success, message)
        """
        if old_key_id not in self.keys or not self.keys[old_key_id]:
            return False, f"old key {old_key_id} not found"

        if new_key_id not in self.keys or not self.keys[new_key_id]:
            return False, f"new key {new_key_id} not found"

        old_version = self._get_current_state(old_key_id)
        if old_version.state != "valid":
            return False, f"old key {old_key_id} is not in valid state: {old_version.state}"

        now = datetime.utcnow().isoformat() + "Z"
        old_version.state = "rotated"
        old_version.rotated_at = now
        old_version.successor_key_id = new_key_id

        self._log("rotate_key", {
            "old_key_id": old_key_id,
            "new_key_id": new_key_id,
            "reason": reason,
        })
        return True, f"rotated {old_key_id} to {new_key_id}"

    def revoke_key(self, key_id: str, reason: str) -> Tuple[bool, str]:
        """Revoke a key immediately.

        Args:
            key_id: ID of key to revoke
            reason: Reason for revocation (e.g., "compromise", "policy_change")

        Returns:
            (success, message)
        """
        if key_id not in self.keys or not self.keys[key_id]:
            return False, f"key {key_id} not found"

        version = self._get_current_state(key_id)

        now = datetime.utcnow().isoformat() + "Z"
        version.state = "revoked"
        version.revoked_at = now
        version.revocation_reason = reason

        self._log("revoke_key", {"key_id": key_id, "reason": reason})
        return True, f"revoked {key_id}: {reason}"

    def _get_current_state(self, key_id: str) -> KeyVersion:
        """Get the most recent version of a key."""
        return self.keys[key_id][-1]

    def get_key_for_verification(
        self,
        key_id: str,
        at_time: Optional[str] = None,
    ) -> Optional[KeyVersion]:
        """Retrieve a key for verification purposes.

        Args:
            key_id: Key identifier
            at_time: Optional ISO 8601 timestamp; if provided, returns the key
                     state valid at that time (useful for verifying historical receipts)

        Returns:
            KeyVersion if found and valid, None otherwise
        """
        if key_id not in self.keys:
            return None

        versions = self.keys[key_id]
        if not versions:
            return None

        if at_time is None:
            # Get current state
            current = versions[-1]
            if current.state == "valid":
                return current
            # If current state is rotated/revoked, may still be usable for verification
            # depending on policy (for historical receipts)
            return None

        # Historical verification: find the key state at the given time
        at_datetime = datetime.fromisoformat(at_time.replace("Z", "+00:00"))
        for version in reversed(versions):
            created = datetime.fromisoformat(version.created_at.replace("Z", "+00:00"))
            if created <= at_datetime:
                # Check if the key was valid at that time
                if version.rotated_at:
                    rotated = datetime.fromisoformat(version.rotated_at.replace("Z", "+00:00"))
                    if at_datetime >= rotated:
                        continue  # Key was already rotated by this time
                if version.revoked_at:
                    revoked = datetime.fromisoformat(version.revoked_at.replace("Z", "+00:00"))
                    if at_datetime >= revoked:
                        continue  # Key was revoked by this time
                if version.valid_until:
                    valid_until = datetime.fromisoformat(version.valid_until.replace("Z", "+00:00"))
                    if at_datetime >= valid_until:
                        continue  # Key expired by this time
                return version

        return None

    def verify_receipt_with_key_history(
        self,
        key_id: str,
        receipt_timestamp: str,
        verify_fn,  # Callable that performs cryptographic verification
    ) -> Tuple[bool, str]:
        """Verify a receipt using the key state at the time the receipt was issued.

        Args:
            key_id: ID of signing key
            receipt_timestamp: ISO 8601 timestamp of when receipt was issued
            verify_fn: Callable (key_version) -> bool

        Returns:
            (is_valid, message)
        """
        key = self.get_key_for_verification(key_id, at_time=receipt_timestamp)
        if key is None:
            return False, f"no valid key found for {key_id} at {receipt_timestamp}"

        try:
            is_valid = verify_fn(key)
            if is_valid:
                return True, f"receipt verified with {key_id}"
            else:
                return False, f"cryptographic verification failed for {key_id}"
        except Exception as e:
            return False, f"verification error: {str(e)}"

    def export_keystore(self) -> Dict[str, Any]:
        """Export full keystore as JSON-serializable dict."""
        exported_keys = {}
        for key_id, versions in self.keys.items():
            exported_keys[key_id] = [asdict(v) for v in versions]

        return {
            "keystore": exported_keys,
            "history": self.history,
            "exported_at": datetime.utcnow().isoformat() + "Z",
        }

    def import_keystore(self, data: Dict[str, Any]):
        """Import a keystore from exported JSON."""
        if "keystore" not in data:
            raise ValueError("imported data missing 'keystore'")

        for key_id, versions_list in data["keystore"].items():
            self.keys[key_id] = [
                KeyVersion(**v) for v in versions_list
            ]

        if "history" in data:
            self.history = data["history"]

    def _log(self, event: str, details: Dict[str, Any]):
        """Log an event to the audit history."""
        self.history.append({
            "event": event,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "details": details,
        })

    def list_keys(self) -> List[Dict[str, Any]]:
        """List all keys and their current state."""
        result = []
        for key_id, versions in self.keys.items():
            if versions:
                current = versions[-1]
                result.append({
                    "key_id": key_id,
                    "algorithm": current.algorithm,
                    "state": current.state,
                    "created_at": current.created_at,
                    "rotated_at": current.rotated_at,
                    "revoked_at": current.revoked_at,
                    "version_count": len(versions),
                })
        return result


def example_usage():
    """Example: manage key rotation and verify historical receipts."""
    store = KeyStore()

    # Issuer creates initial key (2024-01)
    key_v1 = store.add_key(
        "signer_2024_01",
        "ed25519",
        "abcd" * 16,  # 32-byte public key as hex
        valid_until="2025-01-01T00:00:00Z",
    )
    print(f"Created key: {key_v1.key_id} (state: {key_v1.state})")

    # Receipt issued with v1 key
    receipt_timestamp = "2024-06-15T10:00:00Z"
    print(f"Receipt issued at: {receipt_timestamp} with key v1")

    # Later: routine key rotation (2025-01)
    key_v2 = store.add_key(
        "signer_2025_01",
        "ed25519",
        "efgh" * 16,
        valid_until="2026-01-01T00:00:00Z",
    )
    success, msg = store.rotate_key("signer_2024_01", "signer_2025_01", reason="routine rotation")
    print(f"Rotation: {msg}")

    # Verification of old receipt uses the key state from June 2024
    key_for_receipt = store.get_key_for_verification("signer_2024_01", at_time=receipt_timestamp)
    print(f"Key for receipt verification: {key_for_receipt.key_id if key_for_receipt else 'NOT FOUND'}")

    # Export keystore
    exported = store.export_keystore()
    print(f"\nExported keystore (formatted):")
    print(json.dumps(exported, indent=2))

    # List all keys
    print(f"\nKeystore summary:")
    for k in store.list_keys():
        print(f"  {k['key_id']}: {k['state']} ({k['version_count']} version)")


if __name__ == "__main__":
    example_usage()
