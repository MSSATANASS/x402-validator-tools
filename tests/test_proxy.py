"""Tests for the proxy middleware — config parsing, target extraction,
proxy_handler happy/error paths."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from proxy.middleware import (
    ProxyConfig,
    _extract_target,
    _filtered_request_headers,
    _filtered_response_headers,
    build_app,
)


# Target extraction (unit-tested via the proxy integration tests below to
# avoid aiohttp make_mocked_request routing nuances.)
class TestExtractTarget:
    """Pure-function unit tests for path conventions."""

    def test_url_with_scheme_unchanged(self) -> None:
        # Build a fake aiohttp.web.Request-like with match_info set explicitly,
        # which exercises the same branch the real handler hits.
        from unittest.mock import MagicMock
        req = MagicMock()
        req.match_info = {"path": "https://example.com/api"}
        assert _extract_target(req) == "https://example.com/api"

    def test_url_without_scheme_gets_https(self) -> None:
        from unittest.mock import MagicMock
        req = MagicMock()
        req.match_info = {"path": "example.com/api"}
        assert _extract_target(req) == "https://example.com/api"

    def test_empty_path_returns_none(self) -> None:
        from unittest.mock import MagicMock
        req = MagicMock()
        req.match_info = {"path": ""}
        assert _extract_target(req) is None


# Header filtering
class TestHeaderFiltering:
    def test_response_filters_content_length(self) -> None:
        out = _filtered_response_headers({
            "Transfer-Encoding": "chunked",
            "Content-Encoding": "gzip",
            "Content-Length": "42",
            "Content-Type": "application/json",
        })
        assert "Transfer-Encoding" not in out
        assert "Content-Encoding" not in out
        assert "Content-Length" not in out
        assert out["Content-Type"] == "application/json"


# Config parsing
class TestProxyConfig:
    def test_defaults(self) -> None:
        cfg = ProxyConfig()
        assert cfg.listen_host == "0.0.0.0"
        assert cfg.listen_port == 8080
        assert cfg.on_fail == "rewrite_402"
        assert cfg.validation_timeout == 10.0

    def test_from_yaml_missing_returns_default(self, tmp_path) -> None:
        cfg = ProxyConfig.from_yaml(str(tmp_path / "missing.yaml"))
        assert cfg.listen_port == 8080

    def test_from_yaml_overrides(self, tmp_path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("listen_port: 9999\non_fail: pass_through\n")
        cfg = ProxyConfig.from_yaml(str(path))
        assert cfg.listen_port == 9999
        assert cfg.on_fail == "pass_through"


# App integration test — uses aiohttp test utils
class TestProxyApp:
    @pytest.mark.asyncio
    async def test_root_returns_status(self) -> None:
        app = build_app(ProxyConfig())
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/")
            assert r.status == 200
            body = await r.json()
            assert body["service"] == "x402-proxy"
            assert body["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health(self) -> None:
        app = build_app(ProxyConfig())
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/health")
            assert r.status == 200

    @pytest.mark.asyncio
    async def test_forward_missing_target(self) -> None:
        app = build_app(ProxyConfig())
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/forward/")
            assert r.status == 400

    @pytest.mark.asyncio
    async def test_forward_pass_through(self) -> None:
        """Simulate: upstream returns 200, validation passes, proxy returns 200
        with validation headers attached."""
        # Mock the proxy internals
        fake_validation = {
            "url": "https://example.com",
            "overall_status": "PASS",
            "summary": "ok",
            "checks": [],
            "timestamp": "2026-07-27T12:00:00Z",
        }
        with patch("proxy.middleware._validate_upstream", AsyncMock(return_value=fake_validation)):
            with patch("proxy.middleware._fetch_upstream", AsyncMock(return_value=(
                200, b"hello", {"Content-Type": "text/plain"}
            ))):
                app = build_app(ProxyConfig(on_fail="rewrite_402"))
                async with TestClient(TestServer(app)) as client:
                    r = await client.get("/forward/https://example.com")
                    assert r.status == 200
                    assert await r.text() == "hello"
                    assert r.headers.get("X-Validation-Status") == "PASS"
                    assert "ok" in r.headers.get("X-Validation-Report", "")

    @pytest.mark.asyncio
    async def test_forward_validation_fail_rewrites_to_402(self) -> None:
        fake_validation = {
            "url": "https://example.com",
            "overall_status": "FAIL",
            "summary": "no manifest",
            "checks": [{"name": "manifest_discovery", "status": "FAIL", "message": "missing"}],
            "timestamp": "2026-07-27T12:00:00Z",
        }
        with patch("proxy.middleware._validate_upstream", AsyncMock(return_value=fake_validation)):
            with patch("proxy.middleware._fetch_upstream", AsyncMock(return_value=(
                200, b'{"accepts": []}', {"Content-Type": "application/json"}
            ))):
                app = build_app(ProxyConfig(on_fail="rewrite_402"))
                async with TestClient(TestServer(app)) as client:
                    r = await client.get("/forward/https://example.com")
                    assert r.status == 402
                    body = await r.json()
                    assert body["status"] == "validation_failed"
                    assert body["validation"]["overall_status"] == "FAIL"

    @pytest.mark.asyncio
    async def test_forward_pass_through_mode_keeps_upstream_status(self) -> None:
        fake_validation = {
            "url": "https://example.com",
            "overall_status": "FAIL",
            "summary": "no manifest",
            "checks": [],
            "timestamp": "2026-07-27T12:00:00Z",
        }
        with patch("proxy.middleware._validate_upstream", AsyncMock(return_value=fake_validation)):
            with patch("proxy.middleware._fetch_upstream", AsyncMock(return_value=(
                200, b"raw upstream body", {"Content-Type": "text/plain"}
            ))):
                app = build_app(ProxyConfig(on_fail="pass_through"))
                async with TestClient(TestServer(app)) as client:
                    r = await client.get("/forward/https://example.com")
                    # pass_through keeps upstream status
                    assert r.status == 200
                    assert await r.text() == "raw upstream body"
                    assert r.headers.get("X-Validation-Status") == "FAIL"
