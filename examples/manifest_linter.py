"""Manifest linter and JSON Schema validator for /.well-known/x402.

Validates manifest structure, CAIP-2 compliance, and deployment best practices.
Provides detailed error reporting and remediation guidance for exchanges.
"""

import json
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class LintError:
    """A single linting error or warning."""
    level: str  # "error", "warning", "info"
    field: str
    message: str
    remediation: str


CAIP2_RE = re.compile(r"^[a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}$")


class ManifestLinter:
    """Linter for x402 manifests."""

    def __init__(self):
        self.errors: List[LintError] = []

    def clear(self):
        """Clear previous lint results."""
        self.errors = []

    def lint(self, manifest: Dict[str, Any]) -> List[LintError]:
        """Run full linting on a manifest.

        Returns:
            List of LintError objects (empty if valid)
        """
        self.clear()

        self._check_top_level(manifest)
        self._check_x402_version(manifest)
        self._check_accepts(manifest)
        self._check_resource(manifest)
        self._check_nonce(manifest)
        self._check_cdni_compatibility(manifest)

        return self.errors

    def _error(self, level: str, field: str, message: str, remediation: str):
        """Add an error."""
        self.errors.append(LintError(
            level=level,
            field=field,
            message=message,
            remediation=remediation,
        ))

    def _check_top_level(self, manifest: Dict[str, Any]):
        """Check top-level structure."""
        if not isinstance(manifest, dict):
            self._error(
                "error", "root",
                "manifest must be a JSON object",
                "Ensure /.well-known/x402 returns a JSON object, not an array or scalar"
            )

    def _check_x402_version(self, manifest: Dict[str, Any]):
        """Check x402Version field."""
        version = manifest.get("x402Version")

        if version is None:
            self._error(
                "error", "x402Version",
                "x402Version is required",
                "Add x402Version (e.g., '1' or '1.0') to the manifest"
            )
        elif not isinstance(version, (str, int)):
            self._error(
                "error", "x402Version",
                f"x402Version must be string or number, got {type(version).__name__}",
                "Ensure x402Version is a string or number (e.g., '1' or 1)"
            )

    def _check_accepts(self, manifest: Dict[str, Any]):
        """Check accepts array structure."""
        accepts = manifest.get("accepts")

        if accepts is None:
            self._error(
                "error", "accepts",
                "accepts array is required",
                "Add accepts array with at least one payment scheme/network pair"
            )
            return

        if not isinstance(accepts, list):
            self._error(
                "error", "accepts",
                f"accepts must be an array, got {type(accepts).__name__}",
                "Ensure accepts is a JSON array"
            )
            return

        if len(accepts) == 0:
            self._error(
                "warning", "accepts",
                "accepts array is empty",
                "Add at least one accepted payment scheme to enable payments"
            )
            return

        for i, option in enumerate(accepts):
            self._check_accept_option(i, option)

    def _check_accept_option(self, idx: int, option: Dict[str, Any]):
        """Check a single accept option."""
        if not isinstance(option, dict):
            self._error(
                "error", f"accepts[{idx}]",
                f"accept option must be an object, got {type(option).__name__}",
                "Ensure each element in accepts is a JSON object"
            )
            return

        # Required fields
        required = ["scheme", "network", "asset", "payTo"]
        for field in required:
            if field not in option:
                self._error(
                    "error", f"accepts[{idx}].{field}",
                    f"missing required field: {field}",
                    f"Add {field} to this payment option (e.g., scheme='eip191')"
                )

        # Validate network as CAIP-2
        network = option.get("network")
        if network is not None and not isinstance(network, str):
            self._error(
                "error", f"accepts[{idx}].network",
                f"network must be string, got {type(network).__name__}",
                "Ensure network is a string (CAIP-2 format, e.g., 'eip155:1')"
            )
        elif network and not CAIP2_RE.match(network):
            self._error(
                "error", f"accepts[{idx}].network",
                f"network '{network}' is not valid CAIP-2 (expected namespace:reference)",
                "Use CAIP-2 format for network: e.g., 'eip155:1' for Ethereum mainnet"
            )

        # Validate asset is known/documented
        asset = option.get("asset")
        if asset and not self._is_valid_asset(asset):
            self._error(
                "warning", f"accepts[{idx}].asset",
                f"asset '{asset}' is not a recognized standard (e.g., usd, eur, btc)",
                "Consider using standard asset codes (fiat: usd/eur/gbp; crypto: btc/eth/usdc)"
            )

    def _is_valid_asset(self, asset: str) -> bool:
        """Check if asset is a recognized standard."""
        standard_assets = {
            # Fiat
            "usd", "eur", "gbp", "jpy", "aud", "cad",
            # Crypto
            "btc", "eth", "usdc", "usdt", "dai", "bnb", "sol", "xrp",
            # Catch-all for chains
        }
        return asset.lower() in standard_assets or ":" in asset  # allow CAIP-x style

    def _check_resource(self, manifest: Dict[str, Any]):
        """Check resource field (recommended)."""
        if "resource" not in manifest:
            self._error(
                "warning", "resource",
                "resource field is missing",
                "Add resource (e.g., 'exchange API' or URL) to describe the protected resource"
            )
        else:
            resource = manifest.get("resource")
            if not isinstance(resource, str):
                self._error(
                    "error", "resource",
                    f"resource must be string, got {type(resource).__name__}",
                    "Ensure resource is a string describing the protected resource"
                )

    def _check_nonce(self, manifest: Dict[str, Any]):
        """Check optional nonce field and lifecycle."""
        nonce = manifest.get("nonce")

        if nonce is None:
            self._error(
                "info", "nonce",
                "nonce field not present in manifest",
                "Consider adding a nonce for replay protection if using stateful challenges"
            )
            return

        if not isinstance(nonce, str):
            self._error(
                "error", "nonce",
                f"nonce must be string, got {type(nonce).__name__}",
                "Ensure nonce is a string (hex or alphanumeric)"
            )

    def _check_cdni_compatibility(self, manifest: Dict[str, Any]):
        """Check CDN/WAF compatibility (size, structure)."""
        manifest_json = json.dumps(manifest)
        size_bytes = len(manifest_json.encode("utf-8"))

        if size_bytes > 100_000:
            self._error(
                "warning", "root",
                f"manifest is large ({size_bytes} bytes), may cause CDN/WAF issues",
                "Consider moving large data (accepts array) to a separate endpoint or paginating"
            )

        # Check for excessive nesting
        depth = self._max_depth(manifest)
        if depth > 10:
            self._error(
                "warning", "root",
                f"manifest has deep nesting (depth {depth}), may cause parsing issues",
                "Flatten the structure if possible, keeping nesting to < 5 levels"
            )

    def _max_depth(self, obj: Any, current: int = 0) -> int:
        """Compute maximum nesting depth of an object."""
        if isinstance(obj, dict):
            if not obj:
                return current
            return max(self._max_depth(v, current + 1) for v in obj.values())
        elif isinstance(obj, list):
            if not obj:
                return current
            return max(self._max_depth(item, current + 1) for item in obj)
        else:
            return current

    def format_report(self) -> str:
        """Format lint results as a human-readable report."""
        if not self.errors:
            return "✓ Manifest is valid\n"

        by_level = {"error": [], "warning": [], "info": []}
        for err in self.errors:
            by_level[err.level].append(err)

        report = []
        for level in ["error", "warning", "info"]:
            if not by_level[level]:
                continue
            report.append(f"\n{level.upper()}S ({len(by_level[level])}):")
            for err in by_level[level]:
                report.append(f"  {err.field}: {err.message}")
                report.append(f"    → {err.remediation}")

        return "\n".join(report)


def example_usage():
    """Example: lint a manifest."""
    manifest = {
        "x402Version": "1",
        "accepts": [
            {
                "scheme": "eip191",
                "network": "eip155:1",
                "asset": "usd",
                "payTo": "0x...",
            },
            {
                "scheme": "eip191",
                "network": "invalid_network",  # Will fail CAIP-2 check
                "asset": "custom_token",  # Non-standard asset
                "payTo": "0x...",
            },
        ],
    }

    linter = ManifestLinter()
    errors = linter.lint(manifest)
    print(linter.format_report())


if __name__ == "__main__":
    example_usage()
