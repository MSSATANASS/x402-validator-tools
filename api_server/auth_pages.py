"""Server-rendered account routes: signup, login, logout, dashboard.

Follows the landing aesthetic via the shared chrome in api_server.pages.
All mutations require the Neon-backed keystore: without DATABASE_URL the
routes answer 503 (same degradation pattern as the keystore itself).

CSRF: the session cookie is SameSite=Lax, which blocks cross-site form
POSTs from third-party origins.
"""

from __future__ import annotations

import html as _html
import os

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api_server import auth, ratelimit
from api_server.pages import PAGE_CSS, PAGE_FOOTER, PAGE_NAV

router = APIRouter()

SESSION_MAX_AGE = auth.SESSION_TTL_DAYS * 86_400
SIGNUP_DAILY_LIMIT_DEFAULT = 5
LOGIN_DAILY_LIMIT_DEFAULT = 50


# ---------------------------------------------------------------------------
# Page shell + form templates
# ---------------------------------------------------------------------------

_AUTH_CSS = """
.auth-card{max-width:460px;margin:0 auto;background:#fff;border:1px solid var(--glass-border);border-radius:14px;padding:32px;}
.auth-card h1{font-size:1.8rem;margin-bottom:6px;}
.field{margin:14px 0;}
.field label{display:block;font-size:0.85rem;color:var(--fg-70);margin-bottom:6px;}
.field input{width:100%;padding:10px 12px;border:1px solid var(--glass-border);border-radius:8px;font:inherit;background:var(--bg);}
.form-btn{margin-top:18px;width:100%;background:#0a0a0a;color:#fff;border:none;padding:12px;border-radius:999px;font-weight:600;font-size:0.95rem;cursor:pointer;font-family:inherit;}
.form-btn:hover{background:var(--accent-hover);}
.form-error{border:1px solid #fecaca;background:#fef2f2;color:#991b1b;border-radius:8px;padding:10px 12px;font-size:0.88rem;margin:12px 0;}
.form-note{font-size:0.85rem;color:var(--fg-60);margin-top:14px;text-align:center;}
.form-note a{color:var(--accent);}
table.keys{width:100%;border-collapse:collapse;margin:14px 0;}
table.keys th,table.keys td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--glass-border);font-size:0.88rem;vertical-align:middle;}
.mini-btn{background:none;border:1px solid var(--glass-border);border-radius:8px;padding:4px 10px;font-size:0.8rem;cursor:pointer;font-family:inherit;color:var(--fg-70);}
.mini-btn:hover{border-color:#991b1b;color:#991b1b;}
.key-box{background:#0a0a0a;color:#e5e5e5;border-radius:10px;padding:14px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;word-break:break-all;margin:14px 0;user-select:all;}
"""

_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · x402 validator</title>
<link rel="icon" type="image/png" href="/static/favicon-32.png">
<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>__PAGE_CSS____AUTH_CSS__</style>
</head>
<body>
__PAGE_NAV__
<div class="wrap">
__BODY__
</div>
__PAGE_FOOTER__
</body>
</html>"""

_SIGNUP_FORM = """
<div class="auth-card">
  <h1>Create account</h1>
  <p>Free plan included — upgrade anytime.</p>
  __ERROR__
  <form method="post" action="/signup">
    <div class="field"><label for="email">Email</label>
      <input id="email" name="email" type="email" required value="__EMAIL__"></div>
    <div class="field"><label for="password">Password (min 8 characters)</label>
      <input id="password" name="password" type="password" required></div>
    <button class="form-btn" type="submit">Sign up</button>
  </form>
  <div class="form-note">Already have an account? <a href="/login">Log in</a></div>
</div>"""

_LOGIN_FORM = """
<div class="auth-card">
  <h1>Log in</h1>
  __ERROR__
  <form method="post" action="/login">
    <div class="field"><label for="email">Email</label>
      <input id="email" name="email" type="email" required value="__EMAIL__"></div>
    <div class="field"><label for="password">Password</label>
      <input id="password" name="password" type="password" required></div>
    <button class="form-btn" type="submit">Log in</button>
  </form>
  <div class="form-note">No account yet? <a href="/signup">Sign up</a></div>
</div>"""


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _SHELL.replace("__PAGE_CSS__", PAGE_CSS)
        .replace("__AUTH_CSS__", _AUTH_CSS)
        .replace("__PAGE_NAV__", PAGE_NAV)
        .replace("__PAGE_FOOTER__", PAGE_FOOTER)
        .replace("__TITLE__", _html.escape(title))
        .replace("__BODY__", body),
        status_code=status_code,
    )


def _error_box(message: str) -> str:
    return f'<div class="form-error">{_html.escape(message)}</div>'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client and request.client.host
            else "unknown")


def _cookie_secure(request: Request) -> bool:
    """Secure flag follows the public scheme (Render sends x-forwarded-proto;
    TestClient/local http stays cookie-compatible)."""
    fwd = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    proto = fwd or request.url.scheme
    return proto == "https"


def _attach_session(response, token: str, secure: bool) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE, token,
        max_age=SESSION_MAX_AGE, httponly=True, secure=secure,
        samesite="lax", path="/",
    )


def current_user(request: Request):
    """The session user dict (id/email/plan_id) or None."""
    token = request.cookies.get(auth.SESSION_COOKIE)
    if not token:
        return None
    store = auth.get_user_store()
    if store is None:
        return None
    return store.get_session_user(token)


def _require_store():
    store = auth.get_user_store()
    if store is None:
        raise HTTPException(
            503, "Login requires the database backend (DATABASE_URL is not set)"
        )
    return store


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_form(request: Request) -> HTMLResponse:
    _require_store()
    return _page("Sign up", _SIGNUP_FORM.replace("__ERROR__", "")
                 .replace("__EMAIL__", ""))


@router.post("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_submit(request: Request,
                        email: str = Form(...), password: str = Form(...)):
    store = _require_store()
    ip = _client_ip(request)
    limit = int(os.environ.get("SIGNUP_DAILY_LIMIT", SIGNUP_DAILY_LIMIT_DEFAULT))
    if not ratelimit.get_limiter().allow(f"signup:{ip}", limit):
        raise HTTPException(
            429, "Too many signups from this IP. Try again tomorrow."
        )
    email_n = auth.normalize_email(email)
    if not auth.is_valid_email(email_n):
        return _bad_signup("Please enter a valid email address.", email)
    if not auth.is_valid_password(password):
        return _bad_signup(
            "Password must be between 8 and 200 characters.", email
        )
    try:
        user_id = store.create_user(email_n, password)
    except auth.DuplicateEmail:
        return _bad_signup(
            "An account with that email already exists.", email, status=409
        )
    token = store.create_session(user_id)
    response = RedirectResponse("/dashboard", status_code=303)
    _attach_session(response, token, _cookie_secure(request))
    return response


def _bad_signup(message: str, email: str, status: int = 400) -> HTMLResponse:
    body = (_SIGNUP_FORM
            .replace("__ERROR__", _error_box(message))
            .replace("__EMAIL__", _html.escape(email or "")))
    return _page("Sign up", body, status_code=status)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_form(request: Request) -> HTMLResponse:
    _require_store()
    return _page("Log in", _LOGIN_FORM.replace("__ERROR__", "")
                 .replace("__EMAIL__", ""))


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(request: Request,
                       email: str = Form(...), password: str = Form(...)):
    store = _require_store()
    ip = _client_ip(request)
    limit = int(os.environ.get("LOGIN_DAILY_LIMIT", LOGIN_DAILY_LIMIT_DEFAULT))
    if not ratelimit.get_limiter().allow(f"login:{ip}", limit):
        raise HTTPException(
            429, "Too many login attempts from this IP. Try again later."
        )
    user_id = store.authenticate(auth.normalize_email(email), password)
    if user_id is None:
        body = (_LOGIN_FORM
                .replace("__ERROR__", _error_box("Invalid email or password."))
                .replace("__EMAIL__", _html.escape(email or "")))
        return _page("Log in", body, status_code=401)
    token = store.create_session(user_id)
    response = RedirectResponse("/dashboard", status_code=303)
    _attach_session(response, token, _cookie_secure(request))
    return response


@router.post("/logout", include_in_schema=False)
async def logout(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        store = auth.get_user_store()
        if store is not None:
            store.revoke_session(token)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response
