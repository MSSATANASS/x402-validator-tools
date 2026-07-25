import json
import pytest
from unittest.mock import patch, AsyncMock
from aiohttp.test_utils import AioHTTPTestCase

from proxy_middleware import build_app, extract_target
from x402_conformance_engine import AuditReport, ManifestResult, Caip2Result, JsonResilienceResult, BazaarResult
from datetime import datetime, timezone


def make_report(url: str, status: str = "PASS") -> AuditReport:
    return AuditReport(
        target_url=url,
        timestamp=datetime.now(timezone.utc),
        overall_status=status,
        checks=[
            ManifestResult(status="PASS" if status == "PASS" else "FAIL", message="ok"),
            Caip2Result(status="PASS" if status == "PASS" else "FAIL", message="ok"),
            JsonResilienceResult(status="PASS" if status == "PASS" else "FAIL", message="ok"),
            BazaarResult(status="PASS" if status == "PASS" else "FAIL", message="ok"),
        ],
        summary="",
    )


class TestExtractTarget:
    def test_extracts_https_url(self):
        request = type("Req", (), {"match_info": {"path": "https://api.example.com/data"}})()
        assert extract_target(request) == "https://api.example.com/data"

    def test_extracts_without_scheme(self):
        request = type("Req", (), {"match_info": {"path": "api.example.com/data"}})()
        assert extract_target(request) == "https://api.example.com/data"

    def test_empty_path(self):
        request = type("Req", (), {"match_info": {"path": ""}})()
        assert extract_target(request) is None


class TestProxyMiddleware(AioHTTPTestCase):
    async def get_application(self):
        return build_app()

    async def test_root_returns_status(self):
        resp = await self.client.get("/")
        assert resp.status == 200
        data = await resp.json()
        assert data["service"] == "x402-proxy"

    async def test_missing_target_returns_400(self):
        resp = await self.client.get("/forward/")
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data

    async def test_proxy_valid_passthrough(self):
        mock_upstream = (200, b'{"status":"ok"}', {"content-type": "application/json"})
        mock_report = make_report("https://api.example.com", status="PASS")

        with patch("proxy_middleware.fetch_upstream", new_callable=AsyncMock) as mock_fetch, \
             patch("proxy_middleware.X402Auditor") as MockAuditor:
            mock_fetch.return_value = mock_upstream
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value.run_full_audit = AsyncMock(return_value=mock_report)
            MockAuditor.return_value = mock_ctx

            resp = await self.client.get("/forward/https://api.example.com")
            assert resp.status == 200
            assert resp.headers.get("X-Validation-Status") == "PASS"

    async def test_proxy_fail_returns_402(self):
        mock_upstream = (402, json.dumps({"error": "payment required"}).encode(), {"content-type": "application/json"})
        mock_report = make_report("https://api.example.com", status="FAIL")

        with patch("proxy_middleware.fetch_upstream", new_callable=AsyncMock) as mock_fetch, \
             patch("proxy_middleware.X402Auditor") as MockAuditor:
            mock_fetch.return_value = mock_upstream
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__.return_value.run_full_audit = AsyncMock(return_value=mock_report)
            MockAuditor.return_value = mock_ctx

            resp = await self.client.get("/forward/https://api.example.com")
            assert resp.status == 402
            data = await resp.json()
            assert data["status"] == "validation_failed"
            assert resp.headers.get("X-Validation-Status") == "FAIL"
