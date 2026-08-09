"""Unit tests for pure evaluate_batch_settlement_requirements (no HTTP)."""
from __future__ import annotations

import re
import string

from api_server.batch_settlement import (
    CHECK_NAME,
    FINDINGS_CAP,
    SPEC_REF,
    evaluate_batch_settlement_requirements,
)

VALID = {
    "scheme": "batch-settlement",
    "network": "eip155:8453",
    "amount": "100000",
    "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "payTo": "0x1111111111111111111111111111111111111111",
    "maxTimeoutSeconds": 3600,
    "extra": {
        "receiverAuthorizer": "0x2222222222222222222222222222222222222222",
        "withdrawDelay": 900,
        "name": "USDC",
        "version": "2",
    },
}

ZERO = "0x" + ("0" * 40)
TARGET = "https://example.com/pay"


def _eval(payload, *, http_status=402, target_url=TARGET, payload_source="none"):
    return evaluate_batch_settlement_requirements(
        payload,
        http_status=http_status,
        target_url=target_url,
        payload_source=payload_source,
    )


def _findings_fields(result):
    return {f["field"] for f in result["details"]["findings"]}


def _findings_codes(result):
    return {f["code"] for f in result["details"]["findings"]}


def test_exact_only_402_pass_not_applicable():
    payload = {
        "x402Version": 2,
        "accepts": [
            {
                "scheme": "exact",
                "network": "eip155:8453",
                "amount": "1000",
                "asset": VALID["asset"],
                "payTo": VALID["payTo"],
            }
        ],
    }
    r = _eval(payload)
    assert r["check_name"] == CHECK_NAME
    assert r["status"] == "PASS"
    assert r["details"]["applicable"] is False
    assert r["details"]["batch_entries"] == 0
    assert r["details"]["findings_total"] == 0
    assert r["details"]["findings"] == []


def test_full_valid_batch_settlement_pass_applicable():
    payload = {"x402Version": 2, "accepts": [dict(VALID)]}
    r = _eval(payload, payload_source="cold_probe_post")
    assert r["status"] == "PASS"
    assert r["details"]["applicable"] is True
    assert r["details"]["batch_entries"] == 1
    assert r["details"]["findings_total"] == 0
    assert r["details"]["findings"] == []
    assert r["details"]["payload_source"] == "cold_probe_post"
    assert r["details"]["status_code"] == 402
    assert r["details"]["spec_ref"] == SPEC_REF
    assert r["details"]["aliases_used"] == []


def test_missing_receiver_authorizer_fail():
    entry = dict(VALID)
    entry["extra"] = {
        "withdrawDelay": 900,
        "name": "USDC",
        "version": "2",
    }
    r = _eval({"accepts": [entry]})
    assert r["status"] == "FAIL"
    assert r["details"]["applicable"] is True
    assert r["details"]["findings_total"] >= 1
    f0 = r["details"]["findings"][0]
    assert f0["accepts_index"] == 0
    assert f0["field"] == "extra.receiverAuthorizer"
    assert "missing" in f0["code"] or "required" in f0["code"]


def test_withdraw_delay_60_fail():
    entry = dict(VALID)
    entry["extra"] = dict(VALID["extra"], withdrawDelay=60)
    r = _eval({"accepts": [entry]})
    assert r["status"] == "FAIL"
    assert "extra.withdrawDelay" in _findings_fields(r)


def test_network_leading_zero_and_non_evm_fail():
    for net in ("eip155:08453", "solana:mainnet", "base", "eip155:0"):
        entry = dict(VALID, network=net)
        r = _eval({"accepts": [entry]})
        assert r["status"] == "FAIL", net
        assert "network" in _findings_fields(r), net


def test_amount_invalid_forms_fail():
    for amt in ("0", "0.01", "007", "-1", "1.0", "abc", ""):
        entry = dict(VALID, amount=amt)
        r = _eval({"accepts": [entry]})
        assert r["status"] == "FAIL", amt
        assert "amount" in _findings_fields(r), amt


def test_zero_address_on_asset_payto_receiver_fail():
    # asset
    entry = dict(VALID, asset=ZERO)
    r = _eval({"accepts": [entry]})
    assert r["status"] == "FAIL"
    assert "asset" in _findings_fields(r)

    # payTo
    entry = dict(VALID, payTo=ZERO)
    r = _eval({"accepts": [entry]})
    assert r["status"] == "FAIL"
    assert "payTo" in _findings_fields(r)

    # receiverAuthorizer
    entry = dict(VALID)
    entry["extra"] = dict(VALID["extra"], receiverAuthorizer=ZERO)
    r = _eval({"accepts": [entry]})
    assert r["status"] == "FAIL"
    assert "extra.receiverAuthorizer" in _findings_fields(r)


def test_address_length_39_and_41_fail():
    short = "0x" + ("a" * 39)
    long_ = "0x" + ("a" * 41)
    for bad in (short, long_):
        entry = dict(VALID, asset=bad)
        r = _eval({"accepts": [entry]})
        assert r["status"] == "FAIL", bad
        assert "asset" in _findings_fields(r)


def test_extra_null_list_string_fail():
    for bad_extra in (None, [], "not-an-object", 42):
        entry = dict(VALID, extra=bad_extra)
        r = _eval({"accepts": [entry]})
        assert r["status"] == "FAIL", bad_extra
        assert "extra" in _findings_fields(r)


def test_multi_entry_one_good_one_bad():
    bad = dict(VALID)
    bad["extra"] = dict(VALID["extra"])
    del bad["extra"]["receiverAuthorizer"]
    payload = {
        "accepts": [
            dict(VALID),  # index 0 good
            {"scheme": "exact", "network": "eip155:1"},  # ignored
            bad,  # index 2 batch-settlement bad
        ]
    }
    r = _eval(payload)
    assert r["status"] == "FAIL"
    assert r["details"]["batch_entries"] == 2
    assert r["details"]["applicable"] is True
    indices = {f["accepts_index"] for f in r["details"]["findings"]}
    assert 2 in indices
    assert 0 not in indices


def test_asset_transfer_method_absent_pass_garbage_fail():
    entry = dict(VALID)
    # absent → PASS
    r = _eval({"accepts": [entry]})
    assert r["status"] == "PASS"

    entry2 = dict(VALID)
    entry2["extra"] = dict(VALID["extra"], assetTransferMethod="garbage")
    r2 = _eval({"accepts": [entry2]})
    assert r2["status"] == "FAIL"
    assert "extra.assetTransferMethod" in _findings_fields(r2)

    for ok in ("eip3009", "permit2"):
        entry3 = dict(VALID)
        entry3["extra"] = dict(VALID["extra"], assetTransferMethod=ok)
        r3 = _eval({"accepts": [entry3]})
        assert r3["status"] == "PASS", ok


def test_pay_to_alias_only_and_both_differ():
    entry = dict(VALID)
    del entry["payTo"]
    entry["pay_to"] = "0x1111111111111111111111111111111111111111"
    r = _eval({"accepts": [entry]})
    assert r["status"] == "PASS"
    assert "pay_to" in r["details"]["aliases_used"]

    entry2 = dict(VALID)
    entry2["pay_to"] = "0x3333333333333333333333333333333333333333"
    r2 = _eval({"accepts": [entry2]})
    assert r2["status"] == "FAIL"
    # conflict should be reported
    assert any(
        f["field"] in ("payTo", "pay_to") or "pay" in f["field"]
        for f in r2["details"]["findings"]
    )


def test_http_status_not_402_pass_not_applicable():
    r = _eval({"accepts": [dict(VALID)]}, http_status=200)
    assert r["status"] == "PASS"
    assert r["details"]["applicable"] is False
    assert r["details"]["status_code"] == 200

    r2 = _eval(None, http_status=405)
    assert r2["status"] == "PASS"
    assert r2["details"]["applicable"] is False


def test_payload_none_status_402_error():
    r = _eval(None, http_status=402)
    assert r["status"] == "ERROR"
    assert r["details"]["applicable"] is None


def test_findings_cap_25_broken_entries():
    broken = []
    for i in range(25):
        e = dict(VALID)
        e["extra"] = dict(VALID["extra"])
        del e["extra"]["receiverAuthorizer"]
        broken.append(e)
    r = _eval({"accepts": broken})
    assert r["status"] == "FAIL"
    assert r["details"]["findings_total"] == 25
    assert len(r["details"]["findings"]) == FINDINGS_CAP == 20
    # message uses findings_total not capped len
    assert "25" in r["message"]
    assert re.search(r"\b25\b", r["message"])


def test_receiver_authorizer_snake_case_only_fail():
    entry = dict(VALID)
    entry["extra"] = {
        "receiver_authorizer": "0x2222222222222222222222222222222222222222",
        "withdrawDelay": 900,
        "name": "USDC",
        "version": "2",
    }
    r = _eval({"accepts": [entry]})
    assert r["status"] == "FAIL"
    assert "extra.receiverAuthorizer" in _findings_fields(r)


def test_spec_ref_commit_len_40_hex():
    commit = SPEC_REF["commit"]
    assert len(commit) == 40
    assert all(c in string.hexdigits for c in commit)
    assert commit == "266b19d2251356ee958a1f4ffaa4e57aa2007f33"
    r = _eval({"accepts": [dict(VALID)]})
    assert r["details"]["spec_ref"]["commit"] == commit
    assert len(r["details"]["spec_ref"]["commit"]) == 40
