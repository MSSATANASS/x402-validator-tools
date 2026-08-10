# CI OIDC & least-privilege credentials

This repo's GitHub Actions pipelines authenticate to external services with
**OpenID Connect (OIDC)** and short-lived, job-scoped tokens. There are **no
long-lived credentials** committed, stored as repo secrets, or otherwise
required — no registry password, no `CODECOV_TOKEN`, no cloud access keys.

## Why OIDC

A classic CI pipeline stores a static secret (a registry password, a cloud
access key) and hands it to every job. That secret is long-lived, easy to leak,
and painful to rotate. OIDC replaces it with a **workflow identity**: GitHub
issues a signed, short-lived JSON Web Token that describes *who* is running
(`repo`, `ref`, `workflow`, ...). The relying party verifies that token against
GitHub's public OIDC issuer and grants narrowly-scoped, minutes-long access.
Nothing durable is stored on our side.

## Principle of least privilege

Every workflow sets an explicit top-level `permissions` block. Without one, a
workflow inherits the repository default, which can be read/write for the whole
`GITHUB_TOKEN`. We start read-only and let each job opt into exactly the scopes
it needs:

| Workflow / job        | Scopes                                             | Why                                                        |
| --------------------- | -------------------------------------------------- | ---------------------------------------------------------- |
| `docker.yml` (build)  | `contents: read`, `packages: write`, `id-token: write` | checkout, push to GHCR, mint OIDC token for cosign         |
| `test.yml` (pytest)   | `contents: read`, `id-token: write`                | checkout, mint OIDC token for Codecov                      |

## What runs where

### Image publishing → GHCR

On push to `main` and on `v*` tags, `docker.yml` builds and pushes a multi-tag
image to `ghcr.io/mssatanass/x402-validator-tools`. It logs in with the
**automatically provisioned, job-scoped `GITHUB_TOKEN`** (not a stored
password). Pull requests only *build* — the login, push, and signing steps are
gated behind `if: github.event_name != 'pull_request'`, so a fork PR can never
touch the registry or a credential.

### Keyless image signing → Sigstore

After the push, `cosign sign` signs the image **digest** keyless:

1. cosign requests a GitHub OIDC identity token (this is why the job needs
   `id-token: write`);
2. it exchanges that token with Sigstore's **Fulcio** CA for an ephemeral
   signing certificate bound to the workflow identity;
3. it signs the digest and records the signature in the public **Rekor**
   transparency log.

No private key exists to steal or rotate. Verify a published image with:

```bash
cosign verify \
  --certificate-identity-regexp "https://github.com/MSSATANASS/x402-validator-tools/.github/workflows/docker.yml@.*" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/mssatanass/x402-validator-tools:main
```

### Coverage upload → Codecov

`test.yml` uploads coverage with `codecov/codecov-action@v5` and
`use_oidc: true`. Codecov validates the GitHub OIDC token instead of reading a
stored `CODECOV_TOKEN`. The step keeps `fail_ci_if_error: false`, so a Codecov
hiccup never fails the build.

> **One-time setup (no secret):** tokenless upload requires the repository to be
> linked in Codecov with OIDC enabled. This is a dashboard toggle on Codecov's
> side, not a value stored in this repo.

## Guardrail test

`tests/test_ci_oidc.py` parses the workflow files and fails CI if the posture
regresses — for example if a workflow drops its explicit `permissions`,
references any secret other than the built-in `GITHUB_TOKEN`, stops signing
images keyless, or stops using Codecov OIDC.

## Extending OIDC to a cloud provider

The flows above are **self-contained**: they need no cloud account and no
provider/audience/role configuration. If a deploy step to a cloud is added
later (e.g. push to AWS ECR, GCP Artifact Registry, or deploy to a managed
runtime), that provider's OIDC federation must be configured first. That step
needs three pieces of information that only the account owner can supply:

- **provider** — AWS, GCP, or Azure;
- **audience** — the `aud` claim the provider expects (e.g. `sts.amazonaws.com`
  for AWS, the Workload Identity Pool provider URL for GCP);
- **role / identity** — the IAM role ARN (AWS), Workload Identity Provider +
  service account (GCP), or client/tenant IDs (Azure) to assume — scoped to the
  minimum permissions the deploy needs.

Once those are known, a job authenticates with the vendor's OIDC action and
keeps `id-token: write` — still no long-lived credentials. Sketch (AWS):

```yaml
# permissions: { id-token: write, contents: read }
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: arn:aws:iam::<ACCOUNT_ID>:role/<DEPLOY_ROLE>
    aws-region: <REGION>
    audience: sts.amazonaws.com
```
