import base64
import json
from api_server.payment_required import decode_payment_required


def _b64(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def test_body_wins_over_payment_required_header():
    body = {"x402Version": 2, "accepts": [{"scheme": "exact", "from": "body"}]}
    hdr = {"from": "header"}
    out = decode_payment_required(
        body=json.dumps(body),
        headers={"payment-required": _b64(hdr)},
    )
    assert out["accepts"][0]["from"] == "body"


def test_payment_required_wins_over_x_payment_required():
    a = {"src": "payment-required"}
    b = {"src": "x-payment-required"}
    out = decode_payment_required(
        body=None,
        headers={
            "payment-required": _b64(a),
            "x-payment-required": _b64(b),
        },
    )
    assert out["src"] == "payment-required"


def test_header_lookup_is_case_insensitive():
    payload = {"src": "mixed"}
    out = decode_payment_required(
        body="",
        headers={"Payment-Required": _b64(payload)},
    )
    assert out["src"] == "mixed"


def test_malformed_returns_none():
    assert decode_payment_required(body="not-json", headers=None) is None
    assert decode_payment_required(body=None, headers={"payment-required": "!!!"}) is None
