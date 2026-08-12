import importlib.util
import os
import sys
import json

# Cargar el script examples/x402_validators.py como módulo dinámico
HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
MODULE_PATH = os.path.join(ROOT, "examples", "x402_validators.py")

spec = importlib.util.spec_from_file_location("x402_validators", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_is_valid_caip2_ok():
    assert mod.is_valid_caip2("eip155:1")
    assert mod.is_valid_caip2("cosmos:cosmoshub-4")


def test_is_valid_caip2_bad():
    assert not mod.is_valid_caip2(123)
    assert not mod.is_valid_caip2("")
    assert not mod.is_valid_caip2("not:a:good:id")


def test_validate_manifest_shape_minimal_ok():
    manifest = {
        "x402Version": "1",
        "accepts": [
            {"scheme": "exact", "network": "eip155:1", "asset": "USDC", "payTo": "0xabc"}
        ],
        "resource": "/weather"
    }
    errs = mod.validate_manifest_shape(manifest)
    assert errs == []


def test_validate_manifest_shape_errors():
    manifest = {"accepts": "not-an-array"}
    errs = mod.validate_manifest_shape(manifest)
    assert "missing x402Version" in errs
    assert any("accepts must be an array" in e for e in errs)


def test_parse_402_challenge_ok():
    body = json.dumps({"x402Version": "1", "accepts": [], "resource": "/r"})
    obj = mod.parse_402_challenge(body)
    assert obj["x402Version"] == "1"


def test_parse_402_challenge_invalid_json():
    try:
        mod.parse_402_challenge("not json")
        assert False, "should have raised"
    except ValueError as e:
        assert "invalid JSON" in str(e)


def test_parse_402_challenge_missing_keys():
    body = json.dumps({"accepts": []})
    try:
        mod.parse_402_challenge(body)
        assert False
    except ValueError as e:
        assert "missing x402Version" in str(e)
