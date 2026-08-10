"""Guardrails for the CI OIDC / least-privilege posture.

These tests parse the GitHub Actions workflow files and assert the security
properties we care about, so a future edit that silently reintroduces a
long-lived credential or an over-broad token scope fails loudly in CI:

- every workflow declares explicit ``permissions`` (never inherits the
  repository default, which can be read/write);
- the only ``secrets.*`` reference allowed is the built-in, job-scoped
  ``GITHUB_TOKEN`` -- no ``CODECOV_TOKEN``, cloud keys, or registry passwords;
- the container image is published to GHCR and signed keyless via GitHub OIDC;
- coverage is uploaded to Codecov via OIDC rather than an upload token.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
DOCKER = WORKFLOWS / "docker.yml"
TEST = WORKFLOWS / "test.yml"

# The single credential the workflows are allowed to reference. It is minted
# per-job by GitHub, scoped to the run, and expires when the job ends.
ALLOWED_SECRET = "GITHUB_TOKEN"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _secret_refs(text: str) -> set[str]:
    """Every ``secrets.NAME`` referenced in a workflow file."""
    return set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))


def _job_permissions(workflow: dict) -> list[dict]:
    return [job.get("permissions") for job in workflow["jobs"].values()]


def test_workflow_files_exist():
    assert DOCKER.is_file()
    assert TEST.is_file()


def test_every_job_has_explicit_permissions():
    """No workflow may rely on the (potentially write-enabled) default token."""
    for path in (DOCKER, TEST):
        workflow = _load(path)
        assert "permissions" in workflow, f"{path.name}: missing top-level permissions"
        for perms in _job_permissions(workflow):
            assert perms is not None, f"{path.name}: a job inherits default permissions"


def test_no_long_lived_secrets_are_referenced():
    """Only the built-in GITHUB_TOKEN may appear; anything else is long-lived."""
    for path in (DOCKER, TEST):
        refs = _secret_refs(path.read_text())
        offenders = refs - {ALLOWED_SECRET}
        assert not offenders, f"{path.name}: unexpected secret references {sorted(offenders)}"


def test_docker_job_requests_oidc_and_packages_scope():
    build = _load(DOCKER)["jobs"]["build"]
    perms = build["permissions"]
    assert perms.get("id-token") == "write", "cosign keyless signing needs id-token: write"
    assert perms.get("packages") == "write", "publishing to GHCR needs packages: write"
    assert perms.get("contents") == "read", "contents should stay read-only"


def test_docker_publish_and_signing_are_gated_to_non_pr_events():
    """Fork PRs must never push to or sign in the registry."""
    steps = _load(DOCKER)["jobs"]["build"]["steps"]
    guarded = {"Log in to GHCR", "Install cosign", "Sign image (keyless OIDC)"}
    seen = {s.get("name"): s.get("if", "") for s in steps if s.get("name") in guarded}
    assert guarded <= seen.keys(), f"missing guarded steps: {guarded - seen.keys()}"
    for name, condition in seen.items():
        assert "pull_request" in condition, f"step '{name}' is not gated to non-PR events"


def test_docker_uses_cosign_keyless_signing():
    text = DOCKER.read_text()
    assert "cosign sign" in text, "expected a keyless cosign signing step"


def test_codecov_upload_uses_oidc_not_a_token():
    steps = _load(TEST)["jobs"]["pytest"]["steps"]
    upload = next(
        (s for s in steps if str(s.get("uses", "")).startswith("codecov/codecov-action")),
        None,
    )
    assert upload is not None, "codecov upload step not found"
    assert upload["with"].get("use_oidc") is True, "Codecov must upload via OIDC (use_oidc: true)"
    perms = _load(TEST)["jobs"]["pytest"]["permissions"]
    assert perms.get("id-token") == "write", "Codecov OIDC needs id-token: write"
