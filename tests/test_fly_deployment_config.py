"""Regression checks for the public Fly.io deployment contract.

These checks keep the repository deployment-ready without requiring Fly.io
credentials or making any network calls.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FLY_TOML = ROOT / "fly.toml"
LEGACY_PUBLIC_HOST = "x402-validator-tools." + "on" + "render.com"


def _fly_config() -> dict:
    return tomllib.loads(FLY_TOML.read_text(encoding="utf-8"))


def test_fly_api_port_and_component_are_aligned() -> None:
    config = _fly_config()
    env = config["env"]
    service = config["http_service"]

    assert env["COMPONENT"] == "api"
    assert env["PORT"] == str(service["internal_port"])
    assert service["internal_port"] == 8080
    assert service["auto_stop_machines"] == "stop"
    assert service["auto_start_machines"] is True
    assert service["min_machines_running"] == 0


def test_fly_machine_uses_one_unambiguous_memory_configuration() -> None:
    vm = _fly_config()["vm"][0]

    assert vm["cpu_kind"] == "shared"
    assert vm["cpus"] == 1
    assert vm["memory_mb"] == 256
    assert "memory" not in vm


def test_production_url_and_docs_do_not_default_to_render() -> None:
    expected_public_url = "https://x402-validator-tools.fly.dev"
    assert _fly_config()["env"]["PUBLIC_URL"] == expected_public_url
    assert not (ROOT / "render.yaml").exists()

    for relative_path in (
        "README.md",
        "api_server/app.py",
        "api_server/auth_pages.py",
        "api_server/x402_paywall.py",
    ):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert LEGACY_PUBLIC_HOST not in content, relative_path
        assert expected_public_url in content, relative_path
