# Directory cold-probe check (visibilidad en Bazaar) — Diseño

**Fecha:** 2026-08-08 · **Estado:** aprobado por el owner
**Origen:** hilo del canal x402 (2026-08-05/08): desindexación silenciosa de
endpoints cuyos POSTs fríos (sin cuerpo, sin auth — la probe de discovery de
Bazaar/CDP) no reciben `402`.

## Contexto y evidencia

- Razvan [BASE]: su endpoint desapareció de agentic.market; la validación de
  body corría antes del payment gate → la probe fría recibía 400/500 en vez de
  402. Las compras reales funcionaban; el revenue no delataba el problema.
- TomSmart_ai: scan a 4 operadores pagados → 2 nunca muestran challenge a una
  cold probe; ~3.6k entradas de catálogo responden 401/405 al GET raso. Su
  veredicto: el test 402-on-bare-probe es "the single highest-value health
  check an x402 operator can run".
- Gap confirmado en nuestro stack: `x402-conformance-suite` 0.5.2 solo probea
  con GET (`_get_or_reason`) y un POST con body real
  (`check_product_endpoint`). Nada simula la cold probe de directorios.

## Alcance v1 (decisiones del owner)

- Check mínimo: **POST frío sin cuerpo y sin auth**; la matriz completa
  (GET/métodos) es v2 y el monitoreo recurrente es v3.
- Expuesto como **un check más del arreglo `checks[]`** de `/validate` y de
  `/audit-public` (la demo gratis es el gancho: "¿estás invisible en Bazaar?").
- Implementación en **`api_server/visibility.py`** dentro de
  x402-validator-tools (un solo repo): el source del paquete pip
  x402-conformance-suite no está bajo control local, así que contribuirlo al
  engine queda descartado para v1 (candidato v2). Integración en
  `_run_audit()` corrido en paralelo con `asyncio.gather` (cero latencia
  extra). Diseño portable al engine después (misma forma CheckResult).

## Semántica del check (`directory_cold_probe`)

POST al endpoint objetivo sin cuerpo, sin auth, sin headers especiales.

| Respuesta | Resultado | Mensaje al operador |
|---|---|---|
| `402` | PASS | La probe fría de directorios ve tu challenge — indexable. |
| `400/500` | FAIL | Validación de body corre antes del payment gate (el bug documentado): los compradores pagan, el directorio no ve 402 y te desindexa en silencio. |
| `401/403` | FAIL | Un auth gate responde antes del payment gate. |
| `405` | FAIL | POST no permitido; si el recurso es GET-only, la probe POST del directorio no lo verá. |
| `200` | FAIL | POST respondió sin challenge — no hay gate para este método. |
| Error de red/timeout | ERROR | Contrato never-raise, como los checks del engine. |

`details`: `{method, status_code}`. El check responde una sola pregunta:
*¿una probe fría ve tu 402?* La calidad del payload la cubren los checks
existentes (accepts completeness, etc.).

## Integración

- `api_server/visibility.py`: `check_directory_cold_probe(url, timeout)` →
  dict forma CheckResult `{check_name, status, message, details}`; nunca lanza.
- `app.py::_run_audit`: corre audit del engine + probe en paralelo
  (`asyncio.gather`) y devuelve `(report, probe)`; `/validate` y
  `/audit-public` (los dos consumidores) agregan la probe a `checks[]`.
- AI Advisor y `explain` heredan el check sin cambios (leen checks fallidos).
- Landing: actualizar el conteo de checks ("seven" → el número real + 1,
  ~2 ocurrencias incluida la FAQ JSON-LD) y mencionar el check nuevo si hay
  lista visible de checks.

## Tests

- Unidad (`tests/test_visibility.py`, sin DB, MockTransport): 402→PASS;
  400/401/405/200→FAIL con su mensaje específico; error de red→ERROR;
  never-raises.
- Integración: `checks[]` de `/validate` y `/audit-public` incluye
  `directory_cold_probe` (probe parcheada con resultado fijo).

## Fuera de alcance v1

- Matriz GET/otros métodos (v2); monitoreo/alertas (v3); contribución al
  paquete engine; cambios en Stripe/plans/keystore.

## Riesgos y mitigaciones

- Falsos FAIL en recursos GET-only → mensaje del check lo aclara.
- La probe es 1 request por audit (despreciable); `/audit-public` ya está
  rate-limited por IP.
- Deploy: push a main → Render auto-despliega; sin env vars nuevas ni
  dependencias nuevas (httpx ya existe).
