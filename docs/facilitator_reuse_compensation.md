# Compensación por reuso de wallet — decisión y defaults

**Status:** Accepted (defaults iniciales, recalibrables con datos reales)
**Date:** 2026-08-09
**Deciders:** g_leo (owner)
**Depende de:** `facilitator_metrics.py` (ya en `main`, commit `a32fe25`)

## Contexto

El facilitador paga gas por cada settle. Un agente puede reusar la misma
wallet en el mismo endpoint 3-4 veces con saldo mínimo, subsidiando
reintentos/spam sin income proporcional. `facilitator_metrics.py` ya
mide el reuso (`{state, method}` por `wallet_hash` + `endpoint`, ventanas
1h/24h) y el balance neto — este documento fija cómo se traduce esa
medición en un fee.

## Decisión

Adoptar el **Modelo D (híbrido)**: gas puro hasta un soft cap, escalera
después. Moneda del surcharge: **USDC** (no native), porque `accepts[]`
de x402 en Base ya piensa en USDC y evita depender de un oráculo
ETH/USD que hoy no existe en el repo.

```
T       = 3      # primera llamada (1-based) que dispara surcharge
N_soft  = T + 2  # = 5, límite de "solo gas extra" antes de escalar
m       = 1.5    # multiplicador inicial sobre gas estimado/histórico
N_max   = 10     # alerta interna (no bloqueo automático) en 1h

if n < T:
    surcharge_usdc = 0
elif n <= N_soft:
    surcharge_usdc = m * gas_est_usd
else:
    k = n - T + 1
    surcharge_usdc = m * gas_p50_usd * k
```

**Sin hard block todavía.** `N_max` es un umbral de alerta/circuit-breaker
manual, no un rechazo automático — la escalera ya vuelve prácticamente
prohibitivo el reuso extremo sin arriesgar tirar tráfico legítimo por
error mientras no hay semanas de datos reales.

## Por qué estos valores (y no otros)

- **T=3** en vez de T=4: en el ejemplo de juguete del Paso 4, T=4 con
  m=1 seguía en déficit en la 4ª llamada. Empezar más conservador
  (T=3) es más seguro que descubrir en producción que se regalaba
  demasiado gas.
- **m=1.5** como punto de partida: cubre gas + margen de buffer sin
  ser tan agresivo como m=2 desde el día uno.
- Estos números son un **punto de partida, no una calibración final**.
  Se recalibran corriendo `facilitator_export_report.py` sobre datos
  reales y verificando la regla de aceptación:
  `sum(income) - sum(gas_usd) >= 0` en ventana 24h, con
  `>=80%` de las wallets `unique/first_seen` sin surcharge.

## Estados de política

| state | method | significado |
|---|---|---|
| `allowed` | `first_free` | n < T, sin surcharge |
| `allowed` | `surcharge_applied` | T <= n <= N_soft, fee = gas puro (× m) |
| `allowed` | `surcharge_escalated` | n > N_soft, fee en escalera |
| `deferred` | `await_wallet` | no hay wallet aún (pre-settle) |
| `alert` | `reuse_cap_1h` | n > N_max — notifica, no bloquea (flag aparte) |

Nota: `alert` / `reuse_cap_1h` se reportan como **flag** en el resultado de
`evaluate_reuse_surcharge` (`alert=true`, `alert_method=…`) **sin** cambiar
`state`/`method`/`surcharge_usdc` del tramo de fee. El state de fee sigue
siendo `allowed` + method de surcharge correspondiente.

## Contrato de la Fase 4a

```
evaluate_reuse_surcharge(
    count_1h: int,
    gas_est_usd: float,
    gas_p50_usd: float,
    *,
    T: int = 3,
    N_soft: int = 5,
    m: float = 1.5,
    N_max: int = 10,
) -> {"state": str, "method": str, "surcharge_usdc": float,
      "alert": bool, "alert_method": str | None}
```

Implementación: `api_server/facilitator_policy.py` (puro; sin wire a
producción). Función sin I/O, sin RPC, sin DB, sin red. Tests en
`tests/test_facilitator_policy.py`.

## Ejemplo numérico (m=1.5, T=3)

Gas por tx de juguete ≈ **$0.0255** (8.5e-6 ETH × $3000). Cuatro reusos
en 1h (n=1..4):

| n | method | surcharge_usdc (m=1.5) |
|---|--------|-------------------------|
| 1–2 | `first_free` | 0 |
| 3 | `surcharge_applied` | 1.5 × 0.0255 = **0.03825** |
| 4 | `surcharge_applied` | 1.5 × 0.0255 = **0.03825** |
| 5 | `surcharge_applied` (borde N_soft) | 1.5 × 0.0255 = **0.03825** |
| 6 | `surcharge_escalated` | 1.5 × gas_p50 × (6−3+1) = 1.5 × p50 × 4 |

Con gas_est = gas_p50 = 0.0255 y n=4: income surcharge en 3ª+4ª =
2 × 0.03825 = **0.0765** vs gas 4×0.0255 = **0.102** → aún déficit leve;
recalibrar m o T con datos reales (regla de aceptación del doc).

## Fuera de alcance en esta fase

- Wire a settlement/dogfood real (Fase 4b)
- Mutación del quote 402 (Fase 4c)
- Dashboard de política vs balance (Fase 4d)
- Rate-limit de `/validate` (audit HTTP, no on-chain)
- Task #1 (rotación de credenciales) — sigue pendiente, sin relación con esto
