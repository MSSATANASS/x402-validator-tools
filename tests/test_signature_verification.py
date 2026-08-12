import importlib.util
import os
import sys
import json

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)

spec = importlib.util.spec_from_file_location("receipt_utils", os.path.join(ROOT, "examples", "receipt_utils.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

# crypto libs for key generation in tests
import nacl.signing
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes


def test_ed25519_signature_verification():
    body = '{"ok": true}'
    resp_hash = mod.compute_response_hash(body)

    sk = nacl.signing.SigningKey.generate()
    vk = sk.verify_key
    sig = sk.sign(bytes.fromhex(resp_hash)).signature

    receipt = {
        "receiptVersion": "1",
        "responseHash": resp_hash,
        "signature": sig.hex(),
        "signer": vk.encode().hex(),
        "algorithm": "ed25519"
    }

    assert mod.verify_receipt(receipt, body)


def test_ecdsa_signature_verification():
    body = '{"ok": "ecdsa"}'
    resp_hash = mod.compute_response_hash(body)

    priv = ec.generate_private_key(ec.SECP256K1())
    pub = priv.public_key()
    sig = priv.sign(bytes.fromhex(resp_hash), ec.ECDSA(hashes.SHA256()))
    pub_pem = pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    receipt = {
        "receiptVersion": "1",
        "responseHash": resp_hash,
        "signature": sig.hex(),
        "signer": pub_pem,
        "algorithm": "ecdsa-secp256k1"
    }

    assert mod.verify_receipt(receipt, body)
