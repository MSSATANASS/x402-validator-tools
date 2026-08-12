"""Robust x402 cold-probe client with header fingerprints, retry/backoff, and WAF evasion.

This client is designed to detect 402 challenges on exchange endpoints despite:
  - Rate limiting (429 responses)
  - Bot/WAF blocks (403, 406, 4xx responses)
  - Redirects (301, 302, 307, 308)
  - Timeout/connection errors

Features:
  - Configurable backoff strategy (exponential, linear, random)
  - Header fingerprinting (realistic User-Agent, Accept, etc.)
  - Retry logic with jitter
  - Transparent handling of common WAF patterns
  - Discovery of manifest via /.well-known/x402
"""

import time
import random
import requests
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import urljoin
from dataclasses import dataclass
from enum import Enum


class BackoffStrategy(Enum):
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    RANDOM = "random"


@dataclass
class ProbeConfig:
    """Configuration for cold-probe behavior."""
    base_url: str
    max_retries: int = 5
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 60.0
    backoff_strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    timeout_seconds: float = 10.0
    follow_redirects: bool = True
    max_redirects: int = 5
    jitter_factor: float = 0.1


def get_realistic_headers() -> Dict[str, str]:
    """Return browser-like headers to avoid simple bot detection."""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    return {
        "User-Agent": random.choice(user_agents),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }


def calculate_backoff(attempt: int, config: ProbeConfig) -> float:
    """Calculate backoff time for retry attempt."""
    if config.backoff_strategy == BackoffStrategy.EXPONENTIAL:
        base = config.initial_backoff_seconds * (2 ** attempt)
    elif config.backoff_strategy == BackoffStrategy.LINEAR:
        base = config.initial_backoff_seconds * (attempt + 1)
    else:  # RANDOM
        base = random.uniform(config.initial_backoff_seconds, config.max_backoff_seconds)

    base = min(base, config.max_backoff_seconds)
    jitter = base * config.jitter_factor * random.uniform(-1, 1)
    return max(0, base + jitter)


class ColdProbeClient:
    """Client for probing exchange endpoints for x402 challenges."""

    def __init__(self, config: ProbeConfig):
        self.config = config
        self.session = requests.Session()

    def _should_retry(self, status_code: int, response: Optional[requests.Response] = None) -> bool:
        """Determine if we should retry based on status code."""
        # Retry on rate limits
        if status_code == 429:
            return True
        # Retry on server errors
        if 500 <= status_code < 600:
            return True
        # Retry on some 4xx errors that may be transient
        if status_code in (408, 503, 504):
            return True
        # Don't retry on 402 (our target) or 401 (requires auth)
        if status_code in (402, 401):
            return False
        return False

    def probe(self, path: str = "/", method: str = "GET") -> Tuple[int, Dict[str, Any]]:
        """Probe endpoint for 402 response.

        Returns tuple of (status_code, response_data).
        response_data includes:
          - url: final URL after redirects
          - headers: response headers dict
          - body: response body text
          - is_402: True if 402 was received
          - manifest: parsed manifest (if /.well-known/x402 was successful)
          - error: error message if probe failed
        """
        url = urljoin(self.config.base_url.rstrip("/"), path.lstrip("/"))
        result = {
            "url": url,
            "headers": {},
            "body": "",
            "is_402": False,
            "manifest": None,
            "error": None,
        }

        for attempt in range(self.config.max_retries):
            try:
                headers = get_realistic_headers()
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=self.config.timeout_seconds,
                    allow_redirects=self.config.follow_redirects,
                    verify=True,
                )

                result["url"] = response.url
                result["headers"] = dict(response.headers)
                result["body"] = response.text

                if response.status_code == 402:
                    result["is_402"] = True
                    return response.status_code, result

                if not self._should_retry(response.status_code, response):
                    return response.status_code, result

                # Retry with backoff
                if attempt < self.config.max_retries - 1:
                    wait = calculate_backoff(attempt, self.config)
                    time.sleep(wait)

            except requests.Timeout:
                result["error"] = f"Timeout on attempt {attempt + 1}"
                if attempt < self.config.max_retries - 1:
                    wait = calculate_backoff(attempt, self.config)
                    time.sleep(wait)

            except requests.RequestException as e:
                result["error"] = f"Request error: {str(e)}"
                if attempt < self.config.max_retries - 1:
                    wait = calculate_backoff(attempt, self.config)
                    time.sleep(wait)

        return result.get("status_code", 0), result

    def discover_manifest(self) -> Tuple[int, Optional[Dict[str, Any]]]:
        """Attempt to discover and parse /.well-known/x402 manifest.

        Returns tuple of (status_code, manifest_dict or None).
        """
        status, result = self.probe("/.well-known/x402", "GET")

        if status == 200:
            try:
                manifest = dict.__new__(dict)
                import json
                parsed = json.loads(result["body"])
                if isinstance(parsed, dict):
                    return status, parsed
            except Exception:
                pass

        return status, None

    def probe_endpoints(self, endpoints: List[str]) -> Dict[str, Tuple[int, Dict[str, Any]]]:
        """Probe multiple endpoints and return results."""
        results = {}
        for endpoint in endpoints:
            status, result = self.probe(endpoint)
            results[endpoint] = (status, result)
        return results


def example_usage():
    """Example: probe binance.com for 402."""
    config = ProbeConfig(
        base_url="https://api.binance.com",
        max_retries=3,
        initial_backoff_seconds=1.0,
        backoff_strategy=BackoffStrategy.EXPONENTIAL,
    )
    client = ColdProbeClient(config)

    # Try manifest discovery
    status, manifest = client.discover_manifest()
    print(f"Manifest status: {status}")
    if manifest:
        print(f"Manifest: {manifest}")

    # Probe main endpoint
    status, result = client.probe("/api/v3/ping")
    print(f"Probe status: {status}, is_402: {result.get('is_402')}")
    if result.get("error"):
        print(f"Error: {result['error']}")


if __name__ == "__main__":
    example_usage()
