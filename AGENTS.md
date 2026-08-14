# AGENTS.md — x402-validator-tools

## Propósito y límites

Este repositorio publica herramientas alrededor de `x402-conformance-suite`: una API FastAPI que audita endpoints x402, un dashboard Flask y un proxy aiohttp. La API pública de producción es el único componente desplegado en la aplicación de Fly.io; dashboard y proxy son superficies independientes y no se deben iniciar por accidente en el mismo proceso público.

La fuente de verdad es el código y las pruebas del repositorio. Los documentos de planes históricos pueden mencionar proveedores anteriores; no deben usarse como instrucciones de despliegue. La configuración actual de producción está en `fly.toml` y en los secretos administrados fuera de Git.

## Comandos de desarrollo

| Objetivo | Comando |
|---|---|
| Instalar dependencias de desarrollo | `pip install -e ".[dev]"` |
| Ejecutar todas las pruebas | `pytest -q` |
| Ejecutar API local | `x402-api` |
| Ejecutar dashboard local | `x402-dashboard` |
| Ejecutar proxy local | `x402-proxy` |
| Revisar lint | `ruff check api_server proxy dashboard` |
| Revisar tipos | `mypy api_server --ignore-missing-imports` |

Los cambios de veredicto, pago o discovery deben incluir pruebas. Antes de cambiar comportamiento, ejecutar la suite existente; antes de entregar, volver a ejecutar las pruebas relevantes y la suite completa.

## Reglas de conformance y liveness

- Reportar liveness como **`{state, method}`**, nunca como booleano ambiguo.
- Preferir conformance estricto sobre verificadores permisivos.
- `PAYMENT-REQUIRED` es el header x402 canónico; el alias heredado no sustituye al header canónico.
- Todo dato público de volumen debe incluir su participación de concentración cuando corresponda.
- Las rutas de discovery deben permanecer gratuitas y declarar `security: []` en OpenAPI.
- Un `POST /validate` no autenticado debe emitir el challenge `402` antes de que Pydantic produzca un `400` o `422` por el body.
- No anunciar MPP, redes, assets, facilitadores ni ownership proofs que no estén implementados y verificados en runtime.

## Contrato de pago y AgentCash

`POST /validate` tiene acceso dual: un `X-API-Key` válido o un pago x402 verificado por facilitator. El comportamiento por defecto actual es Base (`eip155:8453`) con Base USDC y un precio decimal/atómico configurado mediante `X402_*`; las unidades decimales del OpenAPI y las unidades atómicas del challenge no se deben mezclar.

AgentCash se usa como cliente de prueba, no como dependencia de producción. Antes de una llamada pagada, consultar precio/schema y verificar una red compatible con la wallet. Registrar el coste, limitar reintentos y no copiar wallets, claves privadas ni archivos de AgentCash al repositorio, imágenes o logs.

## Producción en Fly.io

El despliegue público usa `fly.toml`, `Dockerfile` y `COMPONENT=api`. El puerto de la app debe coincidir con `http_service.internal_port`; el archivo actual establece ambos en `8080`. `auto_stop_machines = "stop"`, `auto_start_machines = true` y `min_machines_running = 0` reducen tiempo de cómputo inactivo, pero no garantizan coste cero: verificar siempre facturación, root filesystem, transferencia y allowance de la organización antes de prometer un coste.

Secretos de producción solo se configuran en el gestor de secretos del proveedor. Como mínimo, producción requiere `DATABASE_URL` de Neon, `PUBLIC_URL`, `ADMIN_SECRET`, credenciales de Stripe, `INCEPTION_API_KEY` si se habilita el asesor y los ajustes x402 que correspondan. `INCEPTION_MODEL` y `INCEPTION_REASONING_EFFORT` son overrides opcionales; el valor por defecto del asesor es Mercury 2 con esfuerzo bajo. `REDIS_URL` de Upstash se configura solo cuando se habilite un backend de cache compatible. No guardar secretos en `.env` versionado, commits, issue bodies, logs, `AGENTS.md` ni comandos que queden visibles.

`DATABASE_URL` es obligatorio en producción. El keystore JSON local no es durable entre reemplazos de máquina. Si hay datos existentes en `api_keys.json`, usar `scripts/migrate_keystore_to_db.py`, verificar los resultados y conservar un rollback antes de retirar el origen heredado.

## Stripe y cambios públicos

No actualizar el endpoint de webhook de Stripe ni retirar una URL anterior hasta que el release de Fly responda correctamente en `/health`, `/openapi.json`, `/validate` y una prueba firmada de webhook. Los handlers deben ser idempotentes y los cambios de URL requieren una ventana de rollback. Nunca escribir claves `sk_*`, secretos `whsec_*`, precios o tokens reales en el repositorio.

La URL pública canónica se obtiene de `PUBLIC_URL`. Cualquier referencia de marketing, OpenAPI, canonical tags, checkout redirects y challenges x402 debe usar esa misma URL. Tras un cambio de URL, validar discovery y ejecutar el procedimiento real de reindexación en los directorios externos aplicables; una respuesta HTTP 200 no equivale a estar listado.

## Git y entregas

Antes de un commit, revisar el diff, ejecutar las pruebas aplicables y confirmar que no haya secretos. Para cada commit usar identidad por invocación: `Gael Leonardo Chulim Gongora / mss_ali@users.noreply.github.com`. No hacer push, deploy, cambio de Stripe, rotación de credenciales ni registro externo sin una autorización explícita para esa acción concreta.
