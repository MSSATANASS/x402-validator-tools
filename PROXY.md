# x402 Proxy Middleware

Proxy inverso (aiohttp) que intercepta requests, los reenvía al upstream, ejecuta la auditoría x402 sobre el upstream y agrega headers de validación.

## Inicio rápido

```bash
python main.py proxy
curl http://localhost:8080/forward/https://api.example.com/data
```

## Headers agregados

| Header | Valor | Descripción |
|---|---|---|
| `X-Validation-Status` | `PASS` / `FAIL` / `WARN` | Resultado de la auditoría |
| `X-Validation-Report` | JSON | Reporte completo de checks |

## Comportamiento

- Si la validación pasa → responde con el status original del upstream.
- Si la validación falla → responde `402` con un body JSON que incluye `{ "status": "validation_failed", "validation": {...}, "upstream_response": {...} }`.
- Si el upstream no responde → `502 Bad Gateway`.

## Configuración

Editar `proxy_config.yaml`:

```yaml
listen_host: "0.0.0.0"
listen_port: 8080
validation:
  strict_mode: false
  timeout: 10.0
headers:
  x-validation-status: "X-Validation-Status"
  x-validation-report: "X-Validation-Report"
```

## Producción

Con docker-compose:

```yaml
services:
  proxy:
    build: .
    command: ["python", "main.py", "proxy"]
    ports:
      - "8080:8080"
    volumes:
      - ./proxy_config.yaml:/app/proxy_config.yaml
```
