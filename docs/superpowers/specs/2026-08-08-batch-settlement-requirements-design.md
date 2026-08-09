# Batch settlement requirements check — Diseño

**Fecha:** 2026-08-08 · **Estado:** aprobado por el owner  
**Origen:** spec técnica batch-settlement + [scheme EVM oficial](https://github.com/x402-foundation/x402/blob/main/specs/schemes/batch-settlement/scheme_batch_settlement_evm.md)  
**Check name:** `batch_settlement_requirements`

## Contexto

`batch-settlement` es el scheme x402 v2 que desacopla autorización por request (vouchers EIP-712 off-chain) de la liquidación onchain por lotes. Está en producción sobre Base y es el rail de volumen de micropagos.

El engine (`x402-conformance-suite`) valida `accepts[]` genéricos vía `accepts_completeness` (scheme, network, amount, payTo, resource) pero **no** los campos obligatorios del binding EVM de `batch-settlement` (`extra.receiverAuthorizer`, `extra.withdrawDelay`, domain EIP-712 del token, etc.). Un merchant puede anunciar el scheme y quedar **inusable para clientes SDK** sin que el validator lo diga.

## Alcance v1 (una pregunta)

> Si este endpoint ofrece `scheme: "batch-settlement"`, ¿el PaymentRequired trae los campos EVM mínimos para que un cliente abra canal y firme vouchers?

**Incluye:** forma del 402 / `accepts[]` para entradas batch-settlement.  
**No incluye:** deposit onchain, verificación de firmas de voucher, claim/settle, storage del servidor, health de claim cadence vs `withdrawDelay`, RPC al contrato `x402BatchSettlement`.

## Modelo de status del sistema (confirmado)

El validator y el engine usan **únicamente**:

| Status | Significado |
|--------|-------------|
| `PASS` | Expectativas cumplidas **o** check no aplicable |
| `FAIL` | Defecto que el endpoint debe corregir |
| `CRITICAL_FAIL` | Defecto que impide verificación downstream (engine; no lo usamos en este check v1) |
| `ERROR` | El check no pudo ejecutarse (red, parseo) |

**No existe `WARN`.** Por tanto `extra.name` / `extra.version` van como **FAIL** si faltan: sin ellos el cliente no construye el EIP-712 domain separator del token (bloqueo real de firma, no cosmético). Introducir WARN implicaría cambiar el esquema de respuesta API, landing counts y AI Advisor solo para este check — fuera de alcance.

## Decisión crítica: cero GET *extra* en el camino feliz (alcance honesto)

### Qué se resuelve y qué no

| Pareja de checks | ¿Mismo payload garantizado? |
|------------------|-----------------------------|
| `directory_cold_probe` ↔ `batch_settlement_requirements` | **Sí** en camino feliz: reutilizan el 402 del POST cold (0 fetches extra). |
| `accepts_completeness` (engine, GET) ↔ `batch_settlement_requirements` (POST cold o GET fallback tools) | **No.** El engine sigue haciendo su propio GET. Si el merchant responde distinto a GET vs POST (nonce, expiry, montos dinámicos), A y B pueden ver 402 distintos. **No es un bug del validator** — es divergencia del merchant por método HTTP + deuda multi-GET del engine. |

El diseño **reduce** fetches tools-side y elimina no-determinismo entre cold-probe y este check; **no** unifica el payload con el engine. Eso queda explícito en Riesgos.

### Solución v1 (tools-repo, sin fork del engine pip)

Separar **siempre** fetch de validate:

```
evaluate_batch_settlement_requirements(
    payload: dict | None,
    *,
    http_status: int | None,
    target_url: str,
) -> dict  # CheckResult-shaped
```

- La función **nunca** hace HTTP.
- Toda la lógica de campos vive aquí; es lo que unit-testea el 95% de la suite.

**Orquestación en `_run_audit`:**

1. `asyncio.gather(run_audit(...), check_directory_cold_probe(...))` como hoy (engine + cold POST).
2. **Payload source (en orden de preferencia):**
   1. Si el cold probe obtuvo **HTTP 402** y se puede decodificar PaymentRequired → **reutilizar ese payload** (0 fetches extra).
   2. Si no hay 402 usable del cold probe → **un único GET tools-side** de fallback (misma convención que `accepts_completeness`) y decodificar una vez.
3. Llamar solo a `evaluate_*(payload, http_status=..., target_url=...)`.

Para (1) el cold probe debe **exponer** material de respuesta (status + headers + body) al orquestador. El CheckResult público de cold-probe hacia la API no cambia; el path interno devuelve `(check_result, response_snapshot)` o un helper compartido en `payment_required.py`.

### Deuda explícita (no bloquea v1)

El engine pip sigue haciendo GETs por check. Candidato v2: `AuditReport.payment_required` compartido para que `accepts_completeness` y este check lean del mismo snapshot. Hasta entonces este check no añade un tercer fetch paralelo tools-side y, en cold POST → 402, no añade ningún fetch.

## Decodificación PaymentRequired — precedencia fija

Módulo `api_server/payment_required.py`. Fuentes candidatas; **la primera que decodifique a un objeto JSON gana** (no se fusionan):

1. **Body** — si el body es un objeto JSON (dict).
2. Header **`payment-required`** — base64(JSON), misma tolerancia de padding/urlsafe que el engine.
3. Header **`x-payment-required`** — idem.

**Case-insensitivity (HTTP):** antes de buscar, normalizar **todas** las claves de headers a lowercase. `Payment-Required`, `PAYMENT-REQUIRED` y `payment-required` son el mismo header; igual para `x-payment-required`. El lookup nunca compara con la capitalización original del merchant.

Si body y headers difieren, **gana el body**. Si solo hay headers y ambos existen y difieren, gana `payment-required` (no `x-payment-required`). Documentar en el docstring del decoder; tests: body-wins, payment-required-over-x, y lookup case-insensitive (`Payment-Required` cuenta como `payment-required`).

## Semántica del check

| Situación | Status | `applicable` | Mensaje (idea) |
|-----------|--------|--------------|----------------|
| Timeout / red en el único fetch de fallback | `ERROR` | `null` | No se pudo leer el PaymentRequired |
| `http_status != 402` | `PASS` | `false` | N/A — sin challenge 402 |
| 402 pero payload no decodable | `ERROR` | `null` | PaymentRequired ilegible |
| 402 sin entradas `scheme == "batch-settlement"` | `PASS` | `false` | N/A — no anuncia batch-settlement |
| ≥1 entrada batch-settlement y 0 findings | `PASS` | `true` | N ofertas batch-settlement conformes |
| ≥1 entrada batch-settlement con findings | `FAIL` | `true` | `"{findings_total} finding(s); first: {msg}"` (conteo real, no el cap) |

### `applicable`: tri-state

| Valor | Significado |
|-------|-------------|
| `true` | Sabemos que hay ≥1 entrada `batch-settlement` en el payload decodificado |
| `false` | Sabemos que no aplica (no-402, o 402 sin entradas batch-settlement) |
| `null` | Indeterminado — coincide con `status: ERROR` (fetch fallido o 402 indecodable). Un filtro `applicable == true` **no** mezcla “ofrece batch-settlement” con “no sabemos”. |

No se usa un segundo campo `determinable`; el tri-state en `applicable` basta.

## Validaciones por entrada `accepts[]` con `scheme == "batch-settlement"`

Alineado a la tabla **402 Response (PaymentRequirements)** de la spec EVM pinneada abajo.

### Identidad del scheme

- Comparar `scheme` como string exacta: `"batch-settlement"` (tras strip). No aceptar variantes con espacios u otros guiones.

### Campos de primer nivel

| Campo | Regla |
|--------|--------|
| `network` | Debe matchear `^eip155:([1-9][0-9]*)$` — prefijo `eip155:`, chainId **entero positivo sin ceros a la izquierda**. Otros networks → FAIL (binding v1 = solo EVM). |
| `amount` | String de dígitos **sin** signo, **sin** punto decimal, **sin** ceros a la izquierda. Valor **≥ 1** (`"0"` → FAIL). |
| `asset` | Address EVM; **no** zero address. |
| `payTo` | **Canónico.** Address EVM; **no** zero address. |
| `pay_to` | **Único alias snake_case en v1.** Si `payTo` ausente y `pay_to` presente → aceptar y anotar en `details.aliases_used`. Si ambos presentes y difieren → FAIL. |

### Por qué solo `payTo` tiene alias (no `receiverAuthorizer`)

`payTo` / `pay_to` tiene **precedente histórico en el engine** (`accepts_completeness` y marketplace ya leen ambos). La spec EVM de batch-settlement y los ejemplos oficiales usan **solo** camelCase para `extra.receiverAuthorizer`. No hay evidencia de `receiver_authorizer` en SDKs/docs foundation; inventar un segundo alias sin precedente ensuciaría el check. **Decisión deliberada v1:** alias únicamente donde el resto del validator ya lo hace (`payTo`). Si aparece snake_case real en el wild, se añade en un cambio acotado con test.

### Addresses (común a `asset`, `payTo`/`pay_to`, `extra.receiverAuthorizer`)

- Formato: `0x` + **exactamente 40** hex chars (`[0-9a-fA-F]`). Longitud 39/41 → FAIL.
- Zero address (`0x` + 40 ceros) → FAIL en **los tres**.
- **Checksum EIP-55:** **no exigido en v1.** Lowercase / mixed OK. Riesgo: un cliente que valide EIP-55 puede rechazar un PASS nuestro. Candidato v2: modo estricto.

### `extra` (objeto requerido)

| Campo | Regla |
|--------|--------|
| `extra` presente y **tipo object** (dict). `null`, `[]`, string, number → FAIL |
| `extra.receiverAuthorizer` | Requerido; camelCase canónico; address EVM no-zero. **Sin** alias `receiver_authorizer` en v1 |
| `extra.withdrawDelay` | Requerido; `int` JSON o string de dígitos sin leading zeros. Rango **[900, 2_592_000]** inclusive |
| `extra.name` | Requerido; string no vacío tras strip → FAIL si falta |
| `extra.version` | Requerido; string no vacío tras strip → FAIL si falta |
| `extra.assetTransferMethod` | **Opcional.** Ausente → OK. Presente: solo `"eip3009"` o `"permit2"` (case-sensitive) |

### Findings — forma y cap

Cada finding es un objeto:

```json
{
  "accepts_index": 0,
  "field": "extra.receiverAuthorizer",
  "code": "missing_receiver_authorizer",
  "message": "accepts[0]: extra.receiverAuthorizer is required for batch-settlement (EVM)"
}
```

**Cap de respuesta:** `details.findings` incluye como máximo **`FINDINGS_CAP = 20`** entradas (orden de descubrimiento: índice de `accepts[]` ascendente, luego orden de reglas en la tabla).  
**`details.findings_total`:** conteo real de findings **antes** del cap (puede ser > 20).  
El `message` top-level del check usa **`findings_total`** (no `len(findings)`), p. ej. `"47 finding(s); first: …"`, para que `/audit-public` no pierda la magnitud del problema ni hinche el payload.

## `details` de salida

```json
{
  "status_code": 402,
  "applicable": true,
  "batch_entries": 2,
  "findings": [ /* máx 20 objetos */ ],
  "findings_total": 2,
  "aliases_used": ["pay_to"],
  "payload_source": "cold_probe_post",
  "spec_ref": {
    "scheme": "batch-settlement",
    "binding": "evm",
    "doc": "https://github.com/x402-foundation/x402/blob/266b19d2251356ee958a1f4ffaa4e57aa2007f33/specs/schemes/batch-settlement/scheme_batch_settlement_evm.md",
    "commit": "266b19d2251356ee958a1f4ffaa4e57aa2007f33",
    "required_extra_fields": [
      "receiverAuthorizer",
      "withdrawDelay",
      "name",
      "version"
    ]
  }
}
```

- `applicable`: `true` | `false` | `null` (tri-state; ver arriba).
- `payload_source`: `"cold_probe_post"` | `"fallback_get"` | `"none"`.
- `spec_ref.commit`: **SHA completo de 40 hex chars** del commit del repo foundation que pinnea el archivo de la spec EVM. Constante en código (`SPEC_REF_COMMIT = "266b19d2…"` → en implementación el literal **completo**, sin elipsis ni forma corta de 7/12 chars). **No** se resuelve en runtime ni se pinnea a `main@fecha`. Actualizar el SHA solo cuando se cambie deliberadamente la tabla de validaciones. Un test unitario puede afirmar `len(commit) == 40` y `all(c in string.hexdigits for c in commit)`.

**Pin actual (al escribir este diseño):**

- Repo: `x402-foundation/x402`
- Path: `specs/schemes/batch-settlement/scheme_batch_settlement_evm.md`
- Commit (40 hex, copiar tal cual a la constante):

  `266b19d2251356ee958a1f4ffaa4e57aa2007f33`

## Integración

| Pieza | Cambio |
|--------|--------|
| `api_server/payment_required.py` | Decode con precedencia body > `payment-required` > `x-payment-required`. |
| `api_server/batch_settlement.py` | `evaluate_*` puro; `FINDINGS_CAP = 20`; `SPEC_REF` constante con commit SHA. |
| `api_server/visibility.py` | Snapshot de respuesta al orquestador; shape API del cold check sin cambios. |
| `api_server/app.py` `_run_audit` | Tras gather: resolver payload → evaluate → `(report, cold_probe, batch_check)`. |
| `/validate`, `/audit-public` | Append a `checks[]`. |
| Landing / FAQ JSON-LD | **eight → nine** checks. |
| AI Advisor / explain | Sin cambios de esquema. |

## Tests

### Unit — `evaluate_*` (sin HTTP)

1. exact-only 402 → PASS, `applicable: false`
2. batch-settlement completo → PASS, `applicable: true`
3. falta `receiverAuthorizer` → FAIL + `accepts_index`
4. `withdrawDelay` 60 → FAIL
5. network no-EVM / `eip155:08453` → FAIL
6. `amount` `"0.01"` / `"0"` / `"007"` / `"-1"` / no-dígitos → FAIL
7. zero address en asset / payTo / receiverAuthorizer → FAIL
8. address length 39 y 41 → FAIL
9. `extra` null / `[]` / string → FAIL
10. multi-entry: válida + inválida → FAIL, índices correctos, `batch_entries == 2`
11. `assetTransferMethod` ausente → PASS; basura → FAIL
12. solo `pay_to` → PASS + `aliases_used`; ambos distintos → FAIL
13. `http_status != 402` → PASS, `applicable: false`
14. payload `None` + status 402 → ERROR, `applicable: null`
15. **>20 findings** (p. ej. 25 entradas rotas) → `len(findings) == 20`, `findings_total == 25`, message usa 25
16. **no** acepta `receiver_authorizer` snake_case como alias (missing camelCase → FAIL)

### Unit — `payment_required` decode

17. Body gana sobre header `payment-required` cuando difieren  
18. `payment-required` gana sobre `x-payment-required` cuando body no decodifica  
19. Solo `x-payment-required` → OK  
19b. Header `Payment-Required` (mixed case) se trata igual que `payment-required`  
19c. `SPEC_REF["commit"]` tiene longitud 40 y solo hex

### Integración / orquestación

20. Cold POST 402 batch OK → PASS, `payload_source == cold_probe_post`, **0 GET** tools-side (contador mock)  
21. Cold POST 405 + GET fallback 402 exact-only → PASS N/A, `payload_source == fallback_get`  
22. `/validate` incluye `batch_settlement_requirements` en `checks[]`

### Regresión

- Suite sin DB verde; strings “eight” → “nine” en landing/tests donde aplique.

## Fuera de alcance v1

- WARN status  
- Exigir EIP-55  
- Alias `receiver_authorizer`  
- Unificar payload con el engine (compartir GET de `accepts_completeness`)  
- Voucher signing / cumulative amount / onchain  
- Contribución al paquete engine  
- Runtime channel manager / proxy  

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| **Payload de este check ≠ el que vio `accepts_completeness`** si el merchant responde distinto a **GET vs POST** (o entre GETs del engine) | Documentado: no es bug del validator. Unificar solo con v2 engine snapshot. Operadores que vean PASS/FAIL cruzados entre checks deben comparar método HTTP y re-auditar. |
| Spec foundation cambia campos required | `spec_ref.commit` (SHA inmutable) + actualizar tabla al cambiar el check |
| Body vs headers conflictivos | Precedencia fija body > payment-required > x-payment-required |
| Payload findings enorme en `/audit-public` | `FINDINGS_CAP=20` + `findings_total` |
| Checksum EIP-55 no validado | Documentado; v2 opcional |
| Engine multi-GET residual | Aceptado; no empeorado en path cold-402 |
| exact-only en marquee | PASS N/A; 9 checks a propósito |

## Resumen de decisiones (reviews owner)

1. Cero GET extra en camino feliz (cold POST); un GET fallback si hace falta. **No** elimina divergencia vs engine GET — riesgo explícito.  
2. Zero address; amount ≥ 1 sin leading zeros; chainId sin leading zeros; payTo canónico / pay_to alias **solo** (precedente engine); sin alias en receiverAuthorizer.  
3. name/version = FAIL; sin WARN en el sistema.  
4. Nueve checks en landing.  
5. Findings con índice + cap 20 + `findings_total`; tests multi-entry, extra no-objeto, decode precedence, cap.  
6. `applicable`: `true` \| `false` \| `null`.  
7. `spec_ref.commit` = SHA `266b19d2251356ee958a1f4ffaa4e57aa2007f33` (no `main@fecha`).  
8. Status: PASS / FAIL / CRITICAL_FAIL / ERROR.  
9. `spec_ref.commit` = SHA **completo** de 40 hex (nunca forma abreviada con `…`).  
10. Lookup de headers case-insensitive (normalizar a lowercase).
