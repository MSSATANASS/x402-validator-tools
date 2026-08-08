# Diseño: Login + cuentas de usuario ligadas a Stripe

**Fecha:** 2026-08-08 · **Estado:** aprobado por el owner (brainstorming)
**Repo:** x402-validator-tools · **Dependencias nuevas:** `argon2-cffi`

## Contexto

Hoy el SaaS no tiene cuentas: las API keys las emite el owner a mano
(`POST /admin/keys` con `X-Admin-Secret`) o Stripe de forma anónima
(el webhook genera la key y se muestra UNA vez en `/success` — si el
comprador la pierde, no hay forma de recuperarla). Esto bloquea el
crecimiento: cada cliente nuevo requiere intervención manual.

**Objetivo:** registro/login self-service con base de usuarios en Neon,
dashboard para gestionar keys y uso, y compras Stripe ligadas a la cuenta.

**No objetivos (v1):** verificación por email (no hay infra de email a $0),
reset de contraseña (soporte manual), portal de facturación, panel de admin.

## Decisiones aprobadas

1. **Dashboard self-service:** el usuario crea/revoca sus keys, ve cuota y
   uso del mes, y mejora de plan con Stripe desde su dashboard.
2. **Registro abierto** email+password, sin verificación de correo;
   anti-spam con rate-limit por IP (módulo `ratelimit` existente).
3. **El flujo anónimo de compra convive:** sin sesión, todo sigue igual
   (key en `/success`); con sesión, la compra se liga a la cuenta.
4. **Enfoque técnico A:** sesiones server-side en Neon + cookies HttpOnly
   (descartados JWT/localStorage y reutilizar `x402_api_keys`).

## Datos (Neon — schema idempotente, patrón `dbkeystore.ensure_schema`)

```sql
CREATE TABLE IF NOT EXISTS x402_users (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email              TEXT NOT NULL,
    password_hash      TEXT NOT NULL,
    plan_id            TEXT NOT NULL DEFAULT 'free',
    stripe_customer_id TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS x402_users_email_idx
    ON x402_users (lower(email));

CREATE TABLE IF NOT EXISTS x402_sessions (
    token_hash TEXT PRIMARY KEY,            -- sha256(token); el token vivo
    user_id    BIGINT NOT NULL REFERENCES x402_users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL         -- now() + 30 días
);

ALTER TABLE x402_api_keys
    ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES x402_users(id)
    ON DELETE SET NULL;
```

Las keys existentes quedan con `user_id NULL` (huérfanas, siguen funcionando;
el admin las gestiona como hoy vía `/admin/keys`).

## Autenticación — módulo nuevo `api_server/auth.py`

`app.py` ya supera las 2300 líneas; la lógica de cuentas vive en su propio
módulo con funciones puras testeables:

- `hash_password(pw) / verify_password(stored, pw)` — argon2id (`argon2-cffi`).
  Largo de contraseña: 8–200 chars.
- `create_user(email, password)` — email normalizado (trim + lower).
  Email duplicado → error.
- `authenticate(email, password)` — error genérico (sin enumerar usuarios).
- `create_session(user_id) -> token` — `secrets.token_urlsafe(32)`; en Neon
  se guarda **solo el sha256** del token.
- `get_session_user(token)` — lookup por hash + expiración; None si inválida.
- `revoke_session(token)` y limpieza oportunista de expiradas.

**Cookie** `x402_session`: `HttpOnly`, `SameSite=Lax` (mitiga CSRF en POSTs
cross-site), `Secure`, `Path=/`, `Max-Age` 30 días.

**Degradación:** sin `DATABASE_URL` (dev local con keystore JSON) las rutas
de auth devuelven 503 con mensaje claro — mismo patrón que el keystore.

## Rutas y páginas (server-rendered, estética de la landing existente)

| Ruta | Comportamiento |
|---|---|
| `GET/POST /signup` | Crea cuenta + sesión → redirect `/dashboard`. Rate-limit: 5/día/IP |
| `GET/POST /login` | Inicia sesión. Rate-limit: 50/día/IP |
| `POST /logout` | Revoca sesión + borra cookie |
| `GET /dashboard` | Email, plan actual, lista de keys del usuario, uso/cuota |
| `POST /dashboard/keys` | Emite key del plan del usuario (`store.issue(plan_id, user_id=...)`); el token completo se muestra una sola vez |
| `POST /dashboard/keys/revoke` | Recibe `kid = sha256(token)[:12]` — nunca el token crudo en el HTML; solo revoca keys del usuario de la sesión |
| `GET /dashboard/upgrade?plan_id=pro\|enterprise` | Crea checkout ligado y redirige a Stripe |

- En el dashboard, los tokens siempre se muestran enmascarados
  (p. ej. `abc123…`); la única excepción es la pantalla de "key recién creada".
- Nav de la landing: "Log in / Create account" sin sesión, "My dashboard"
  con sesión válida (decisión server-side por cookie).
- Reutiliza el módulo `api_server/ratelimit` (rolling 24h por IP) con
  límites propios para signup/login.

## Integración Stripe

- `/dashboard/upgrade` llama a `stripe_integration.create_checkout_session`
  con `client_reference_id="user:<id>"` y `customer_email` del usuario.
  Si el usuario ya tiene `stripe_customer_id`, se pasa como `customer`.
- El webhook `checkout.session.completed` existente se extiende:
  - `client_reference_id` empieza con `user:` → emitir la key con
    `user_id` + `customer_id`, actualizar `users.plan_id` al plan comprado
    y guardar `stripe_customer_id` si llega.
  - Sin `user:` → comportamiento anónimo actual (sin cambios).
  - Idempotencia existente vía `x402_claims` se mantiene.
- `/success`: si el visitante tiene sesión y el claim es suyo → mensaje
  "tu key ya está en tu dashboard" (la key sigue visible una vez, igual que hoy).

## Seguridad

- Contraseñas: argon2id; jamás se loguean ni se guardan en plano.
- Tokens de sesión: solo su sha256 en DB; una fuga de DB no permite suplantar.
- Errores de login genéricos (no revelan si el email existe).
- `kid` en vez de tokens en formularios (el HTML del dashboard nunca
  contiene un token completo).
- Rate-limits en signup y login contra fuerza bruta/abuso.
- `/admin/keys` sigue igual (X-Admin-Secret) — fuera de este cambio.

## Errores y degradación

| Caso | Comportamiento |
|---|---|
| Sin `DATABASE_URL` | rutas de auth → 503 "login requires the database backend" |
| Stripe caído / sin config | `/dashboard/upgrade` → 503 (patrón existente) |
| Sesión expirada/inválida | `/dashboard` → redirect a `/login` |
| Quota llena al crear key | no aplica: la quota es por uso de key, no por cantidad de keys |

## Tests (patrones de `test_dbkeystore.py` / fixtures existentes)

- **Unidad (sin DB):** hash/verify, normalización de email, creación y
  expiración de tokens (pool mockeado o funciones puras).
- **Con `TEST_DATABASE_URL`:** signup → cookie + redirect; email duplicado;
  login con password incorrecta; `/dashboard` exige sesión; crear/listar keys;
  revoke solo de keys propias (kid ajeno → 404); upgrade → redirect a Stripe
  con `client_reference_id=user:<id>` (mock); webhook con `user:<id>` liga la
  key y sube el plan; **regresión:** checkout anónimo sigue igual;
  rate-limit: 6º signup de la misma IP → 429.
- Baseline actual: 100 passed / 10 skipped.

## Rollout

1. Código + tests → pytest verde (incluye suite con Neon vía vault).
2. Commit + push **cuando el owner lo pida** → Render auto-deploya (schema
   idempotente crea las tablas al arrancar; `ALTER TABLE ... IF NOT EXISTS`
   es seguro en vivo).
3. Verificación en producción: registro de prueba, crear key, usarla en
   `/validate`, upgrade flow (sin pagar: verificar el redirect a Stripe).
4. Sin migración de datos: las keys actuales siguen igual.
