import importlib.util
import os
import sys

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)

# load receipt_utils
spec_r = importlib.util.spec_from_file_location("receipt_utils", os.path.join(ROOT, "examples", "receipt_utils.py"))
mod_r = importlib.util.module_from_spec(spec_r)
sys.modules[spec_r.name] = mod_r
spec_r.loader.exec_module(mod_r)

# load facilitator_detector
spec_f = importlib.util.spec_from_file_location("facilitator_detector", os.path.join(ROOT, "examples", "facilitator_detector.py"))
mod_f = importlib.util.module_from_spec(spec_f)
sys.modules[spec_f.name] = mod_f
spec_f.loader.exec_module(mod_f)


def test_verify_binding_and_signature_ok():
    body = "{\"data\": \"hello\"}"
    resp_hash = mod_r.compute_response_hash(body)
    receipt = {
        "receiptVersion": "1",
        "responseHash": resp_hash,
        "signature": resp_hash,
        "signer": "0xdead",
        "algorithm": "placeholder"
    }
    parsed = mod_r.parse_receipt(receipt)
    assert mod_r.verify_binding(parsed, body)
    assert mod_r.verify_signature_placeholder(parsed)


def test_verify_binding_fail():
    body = "{}"
    receipt = {
        "receiptVersion": "1",
        "responseHash": "bad",
        "signature": "also-bad",
        "signer": "0xdead",
        "algorithm": "placeholder"
    }
    parsed = mod_r.parse_receipt(receipt)
    assert not mod_r.verify_binding(parsed, body)
    assert not mod_r.verify_signature_placeholder(parsed)


def test_parse_receipt_errors():
    try:
        mod_r.parse_receipt(123)
        assert False
    except ValueError:
        pass


def test_facilitator_detector_flags():
    m1 = {"on_chain_volume_30d": 50, "reuse_count": 20, "mediator": "exchange"}
    out = mod_f.classify_facilitator(m1)
    assert out["wash_flag"] is True
    assert "low_volume_high_reuse" in out["reasons"] or "mediator_exchange_reuse" in out["reasons"]


def test_facilitator_detector_ok():
    m2 = {"on_chain_volume_30d": 1000000, "reuse_count": 1, "mediator": "external"}
    out2 = mod_f.classify_facilitator(m2)
    assert out2["wash_flag"] is False
