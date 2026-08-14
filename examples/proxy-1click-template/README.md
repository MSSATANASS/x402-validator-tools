# x402 Proxy 1-click template

[![Deploy to Fly.io](https://fly.io/static/images/launch/button.svg)](https://fly.io/launch?repo=https://github.com/MSSATANASS/x402-validator-tools&template=examples/proxy-1click-template/fly.toml)

A minimal Fly.io template that packages the existing `proxy/middleware.py` and `proxy/config.yaml.example` from `x402-validator-tools`. It forwards requests through `/forward/<host>/<path>`, runs the x402 conformance audit, and attaches validation headers to the response. When validation fails, the default `rewrite_402` policy returns an HTTP 402 JSON envelope; set `on_fail: pass_through` to preserve the upstream body while retaining the validation headers.

The included `fly.toml` is configured for a shared CPU machine with automatic stop/start and zero minimum running machines. Check current Fly.io pricing and account eligibility before deployment; this template does not create a database, volume, payment credential, or secret.

## Five steps to deploy

### 1. Clone the repository and enter the template

```bash
git clone https://github.com/MSSATANASS/x402-validator-tools.git
cd x402-validator-tools/examples/proxy-1click-template
```

### 2. Configure the proxy

```bash
cp proxy/config.yaml.example proxy/config.yaml
```

Edit `proxy/config.yaml`. Keep `listen_host: 0.0.0.0` and `listen_port: 8080` for Fly.io. Choose `on_fail: rewrite_402` to turn failed audits into HTTP 402 responses, or `on_fail: pass_through` to forward the upstream body unchanged.

### 3. Create the Fly app without deploying yet

Install `flyctl`, authenticate with `fly auth login`, and choose a globally unique app name:

```bash
fly launch --no-deploy --copy-config --name my-x402-proxy
```

If Fly Launch asks to overwrite the supplied configuration, keep the repository `fly.toml` settings and confirm the app name.

### 4. Deploy the proxy

```bash
fly deploy
```

Verify that the health endpoint responds:

```bash
fly apps open
curl https://my-x402-proxy.fly.dev/health
```

### 5. Call the monetized/upstream API through the proxy

Use `/forward/<host>/<path>` to proxy an upstream endpoint. The middleware validates the upstream x402 response and returns `X-Validation-Status` and `X-Validation-Report` headers:

```bash
curl -i https://my-x402-proxy.fly.dev/forward/api.example.com/x402/resource
```

Replace `api.example.com/x402/resource` with the host and path of the API you control or are authorized to call. The proxy is intentionally minimal and has no authentication layer; do not expose it publicly without adding access controls, rate limits, and an allowlist suitable for your deployment.

## Local smoke test

From this directory:

```bash
python -m compileall proxy
python -m proxy.middleware
```

The second command starts the server and is intended for a local terminal; stop it with `Ctrl-C`. For production deployment, use `fly deploy` so the included `Dockerfile` and `fly.toml` are applied.

## Scope

This template is additive packaging only. It does not modify Stripe integration, Shopify logic, the x402 endpoint validator, authorization evidence reporting, or payment settlement code.