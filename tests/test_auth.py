"""Tests for api_server.auth (user accounts).

Layout: TestPrimitives runs anywhere (no DB). TestUserStoreIntegration
(Task 3) runs only with TEST_DATABASE_URL.
"""

from __future__ import annotations

import hashlib
import os
import secrets

import pytest

from api_server import auth


class TestPrimitives:
    def test_normalize_email(self):
        assert auth.normalize_email("  Foo@Example.COM ") == "foo@example.com"
        assert auth.normalize_email("") == ""

    def test_is_valid_email(self):
        assert auth.is_valid_email("a@b.co")
        assert auth.is_valid_email("user.name+tag@sub.example.org")
        assert not auth.is_valid_email("no-at-sign")
        assert not auth.is_valid_email("a@b")          # no dot in domain
        assert not auth.is_valid_email("a b@c.de")     # space
        assert not auth.is_valid_email("x" * 400)      # too long

    def test_is_valid_password_bounds(self):
        assert not auth.is_valid_password("7chars!")       # 7 chars
        assert auth.is_valid_password("8chars!!")          # 8 chars
        assert auth.is_valid_password("x" * 200)
        assert not auth.is_valid_password("x" * 201)
        assert not auth.is_valid_password("")

    def test_password_hash_roundtrip(self):
        h = auth.hash_password("correct horse battery")
        assert h != "correct horse battery"
        assert h.startswith("$argon2")
        assert auth.verify_password(h, "correct horse battery") is True
        assert auth.verify_password(h, "wrong password") is False

    def test_verify_password_garbage_hash_is_false(self):
        assert auth.verify_password("not-a-real-hash", "whatever") is False

    def test_kid_for_token(self):
        kid = auth.kid_for_token("tok-abc")
        assert len(kid) == auth.KID_LEN
        assert all(c in "0123456789abcdef" for c in kid)
        assert kid == auth.kid_for_token("tok-abc")      # deterministic
        assert kid != auth.kid_for_token("tok-xyz")
