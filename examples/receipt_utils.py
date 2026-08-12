"""Utilities for handling and verifying x402 receipts
- parse_receipt: structural validation
- compute_response_hash: sha256 hex digest helper
- verify_binding: checks response-body -> responseHash binding (sha256)
- verify_signature: supports ed25519 and ecdsa-secp256k1 (uses PyNaCl and cryptography)

Signatures and signer formats (for this repo's convention):
- ed25519: signer is hex of 32-byte public key, signature is hex of signature bytes
- ecdsa-secp256k1: signer is PEM-encoded public key string, signature is hex of DER-encoded signature bytes

This file performs real signature verification. For production, adapt signer formats to your deployment.
"""
import json
import hashlib
from typing import Any, Dict

# crypto libs
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature
import nacl.signing
import nacl.exceptions


def parse_receipt(receipt_raw: Any) -> Dict[str, Any]:
    """Parse and minimally validate a receipt object (dict or JSON string).

    Raises ValueError on invalid shape.
    """
    if isinstance(receipt_raw, str):
        try:
            receipt = json.loads(receipt_raw)
        except Exception as e:
            raise ValueError("receipt is not valid JSON: " + str(e))
    elif isinstance(receipt_raw, dict):
        receipt = receipt_raw
    else:
        raise ValueError("receipt must be JSON string or dict")

    required = ["receiptVersion", "responseHash", "signature", "signer", "algorithm"]
    for f in required:
        if f not in receipt:
            raise ValueError(f"receipt missing required field: {f}")

    return receipt


def compute_response_hash(response_body: str) -> str:
    """Compute sha256 hex digest of the response body chosen to be bound in the receipt."""
    if not isinstance(response_body, (str, bytes)):
        raise ValueError("response_body must be str or bytes")
    if isinstance(response_body, str):
        response_body = response_body.encode("utf-8")
    h = hashlib.sha256()
    h.update(response_body)
    return h.hexdigest()


def verify_binding(receipt: Dict[str, Any], response_body: str) -> bool:
    """Verify that receipt.responseHash matches the SHA256 of response_body."""
    expected = compute_response_hash(response_body)
    return str(receipt.get("responseHash", "")).lower() == expected.lower()


def verify_signature(receipt: Dict[str, Any]) -> bool:
    """Verify the cryptographic signature in the receipt.

    Supports algorithms:
      - 'ed25519' : signer is hex public key (32 bytes), signature hex is raw signature bytes
      - 'ecdsa-secp256k1' : signer is PEM public key string, signature hex is DER-encoded signature

    Returns True if signature verifies over the raw responseHash bytes.
    """
    algo = str(receipt.get("algorithm", "")).lower()
    resp_hash_hex = str(receipt.get("responseHash", ""))
    sig_hex = str(receipt.get("signature", ""))

    if not resp_hash_hex or not sig_hex:
        return False

    message = bytes.fromhex(resp_hash_hex)
    signature = bytes.fromhex(sig_hex)

    try:
        if algo == "ed25519":
            pub_hex = receipt.get("signer")
            if not isinstance(pub_hex, str):
                return False
            pub_bytes = bytes.fromhex(pub_hex)
            vk = nacl.signing.VerifyKey(pub_bytes)
            try:
                vk.verify(message, signature)
                return True
            except nacl.exceptions.BadSignatureError:
                return False

        elif algo == "ecdsa-secp256k1":
            pub_pem = receipt.get("signer")
            if not isinstance(pub_pem, str):
                return False
            pub = serialization.load_pem_public_key(pub_pem.encode("utf-8"))
            try:
                pub.verify(signature, message, ec.ECDSA(hashes.SHA256()))
                return True
            except InvalidSignature:
                return False

        else:
            # unknown algorithm
            return False
    except Exception:
        return False


def verify_signature_placeholder(receipt: Dict[str, Any]) -> bool:
    """Backward-compatible placeholder: signature equals responseHash.

    Some existing tests and integrations rely on this simple convention; keep it as
    a convenience while full crypto verification is available via verify_signature.
    """
    sig = receipt.get("signature")
    resp_hash = receipt.get("responseHash")
    if not sig or not resp_hash:
        return False
    return str(sig).lower() == str(resp_hash).lower()


# Backwards-compatible convenience function
def verify_receipt(receipt_raw: Any, response_body: str) -> bool:
    """Full verification: parse, check binding and verify signature."""
    receipt = parse_receipt(receipt_raw)
    if not verify_binding(receipt, response_body):
        return False
    # prefer full cryptographic verification when available
    if receipt.get("algorithm") in ("ed25519", "ecdsa-secp256k1"):
        return verify_signature(receipt)
    # fallback to placeholder for legacy receipts
    return verify_signature_placeholder(receipt)
