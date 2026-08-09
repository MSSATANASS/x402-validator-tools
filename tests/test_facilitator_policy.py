"""Unit tests for pure evaluate_reuse_surcharge (Fase 4a). No I/O."""

from __future__ import annotations

import pytest

from api_server.facilitator_policy import (
    DEFAULT_M,
    DEFAULT_N_MAX,
    DEFAULT_N_SOFT,
    DEFAULT_T,
    evaluate_reuse_surcharge,
    evaluate_reuse_surcharge_deferred,
)

# Toy gas from docs/facilitator_reuse_compensation.md (~8.5e-6 ETH * $3000)
GAS_USD = 0.0255


def test_defaults_match_doc():
    assert DEFAULT_T == 3
    assert DEFAULT_N_SOFT == 5
    assert DEFAULT_M == 1.5
    assert DEFAULT_N_MAX == 10
    assert DEFAULT_N_SOFT == DEFAULT_T + 2


def test_n_less_than_T_first_free():
    for n in (0, 1, 2):
        r = evaluate_reuse_surcharge(n, GAS_USD, GAS_USD)
        assert r["state"] == "allowed"
        assert r["method"] == "first_free"
        assert r["surcharge_usdc"] == 0.0
        assert r["alert"] is False
        assert r["alert_method"] is None


def test_n_equals_T_surcharge_applied():
    r = evaluate_reuse_surcharge(DEFAULT_T, GAS_USD, GAS_USD)
    assert r["state"] == "allowed"
    assert r["method"] == "surcharge_applied"
    assert r["surcharge_usdc"] == pytest.approx(DEFAULT_M * GAS_USD)
    assert r["surcharge_usdc"] == pytest.approx(0.03825)
    assert r["alert"] is False


def test_n_equals_N_soft_inclusive_surcharge_applied():
    r = evaluate_reuse_surcharge(DEFAULT_N_SOFT, GAS_USD, GAS_USD)
    assert r["method"] == "surcharge_applied"
    assert r["surcharge_usdc"] == pytest.approx(DEFAULT_M * GAS_USD)
    assert r["alert"] is False


def test_n_equals_N_soft_plus_one_escalated_formula():
    n = DEFAULT_N_SOFT + 1  # 6
    p50 = 0.02
    r = evaluate_reuse_surcharge(n, gas_est_usd=GAS_USD, gas_p50_usd=p50)
    k = n - DEFAULT_T + 1  # 6 - 3 + 1 = 4
    assert r["state"] == "allowed"
    assert r["method"] == "surcharge_escalated"
    assert r["surcharge_usdc"] == pytest.approx(DEFAULT_M * p50 * k)
    assert r["surcharge_usdc"] == pytest.approx(1.5 * 0.02 * 4)
    assert r["alert"] is False


def test_n_equals_N_max_plus_one_alert_without_changing_fee():
    n = DEFAULT_N_MAX + 1  # 11
    p50 = 0.02
    r = evaluate_reuse_surcharge(n, GAS_USD, p50)
    k = n - DEFAULT_T + 1
    expected = DEFAULT_M * p50 * k
    assert r["state"] == "allowed"
    assert r["method"] == "surcharge_escalated"
    assert r["surcharge_usdc"] == pytest.approx(expected)
    assert r["alert"] is True
    assert r["alert_method"] == "reuse_cap_1h"


def test_doc_numeric_example_four_reuses_m_1_5():
    """Four reuses with T=3, m=1.5: calls 3–4 are surcharge_applied @ 0.03825."""
    results = [
        evaluate_reuse_surcharge(n, GAS_USD, GAS_USD) for n in range(1, 5)
    ]
    assert results[0]["method"] == "first_free"
    assert results[1]["method"] == "first_free"
    assert results[2]["method"] == "surcharge_applied"
    assert results[3]["method"] == "surcharge_applied"
    assert results[2]["surcharge_usdc"] == pytest.approx(0.03825)
    assert results[3]["surcharge_usdc"] == pytest.approx(0.03825)
    # Income on 3rd+4th vs gas on all four (toy check from doc)
    income = results[2]["surcharge_usdc"] + results[3]["surcharge_usdc"]
    gas_total = 4 * GAS_USD
    assert income == pytest.approx(0.0765)
    assert gas_total == pytest.approx(0.102)
    assert income < gas_total  # still mild deficit — recalibrate later


def test_compare_m_1_vs_m_2_on_fourth_call():
    """Doc toy: m=1 vs m=2 on n=4 (still in applied tier); m=1.5 is default mid."""
    n = 4
    r1 = evaluate_reuse_surcharge(n, GAS_USD, GAS_USD, m=1.0)
    r15 = evaluate_reuse_surcharge(n, GAS_USD, GAS_USD, m=1.5)
    r2 = evaluate_reuse_surcharge(n, GAS_USD, GAS_USD, m=2.0)
    assert r1["method"] == r15["method"] == r2["method"] == "surcharge_applied"
    assert r1["surcharge_usdc"] == pytest.approx(1.0 * GAS_USD)
    assert r15["surcharge_usdc"] == pytest.approx(1.5 * GAS_USD)
    assert r2["surcharge_usdc"] == pytest.approx(2.0 * GAS_USD)


def test_deferred_await_wallet():
    r = evaluate_reuse_surcharge_deferred()
    assert r["state"] == "deferred"
    assert r["method"] == "await_wallet"
    assert r["surcharge_usdc"] == 0.0
    assert r["alert"] is False


def test_invalid_args():
    with pytest.raises(ValueError):
        evaluate_reuse_surcharge(-1, 0.01, 0.01)
    with pytest.raises(ValueError):
        evaluate_reuse_surcharge(1, -0.01, 0.01)
    with pytest.raises(ValueError):
        evaluate_reuse_surcharge(1, 0.01, 0.01, N_soft=2, T=3)
