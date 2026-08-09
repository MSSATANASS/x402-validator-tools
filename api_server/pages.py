"""Shared chrome (CSS / nav / footer) for the server-rendered pages.

Lives here (not in app.py) so secondary page modules (auth_pages) can reuse
it without importing the 2000+ line app module (circular imports).

Visual system mirrors the landing: dark default, brand #FF4D00 / ink #2B2644 /
surface #F5F5F5 (light), optional light via localStorage key ``x402-theme``.
"""

from __future__ import annotations

PAGE_CSS = """
:root,
html[data-theme="dark"] {
  --bg: #0c0b10;
  --fg: #f4f2f0;
  --fg-80: rgba(244,242,240,0.82);
  --fg-70: rgba(244,242,240,0.72);
  --fg-60: rgba(244,242,240,0.58);
  --fg-50: rgba(244,242,240,0.48);
  --accent: #FF8A4D;
  --accent-hover: #FF4D00;
  --ink: #2B2644;
  --brand: #FF4D00;
  --brand-soft: #FF8A4D;
  --surface: #16141c;
  --input-bg: #1c1a24;
  --glass-border: rgba(255,255,255,0.10);
  --card-border: rgba(255,255,255,0.08);
  --nav-bg: rgba(12,11,16,0.82);
  --muted-fill: rgba(255,255,255,0.06);
  color-scheme: dark;
}
html[data-theme="light"] {
  --bg: #F5F5F5;
  --fg: #0a0a0a;
  --fg-80: rgba(10,10,10,0.80);
  --fg-70: rgba(10,10,10,0.70);
  --fg-60: rgba(10,10,10,0.60);
  --fg-50: rgba(10,10,10,0.50);
  --accent: #2B2644;
  --accent-hover: #0a0a0a;
  --ink: #2B2644;
  --brand: #FF4D00;
  --brand-soft: #FF8A4D;
  --surface: #ffffff;
  --input-bg: #ffffff;
  --glass-border: rgba(10,10,10,0.10);
  --card-border: rgba(10,10,10,0.08);
  --nav-bg: rgba(245,245,245,0.78);
  --muted-fill: rgba(10,10,10,0.06);
  color-scheme: light;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  background: var(--bg); color: var(--fg);
  font-family: 'Instrument Sans', sans-serif;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
.serif { font-family: 'Instrument Serif', serif; }
.wrap { max-width: 860px; margin: 0 auto; padding: 96px 24px 64px; }
nav.navbar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 50;
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px;
  padding: 12px 28px;
  background: var(--nav-bg);
  backdrop-filter: blur(18px) saturate(1.2); -webkit-backdrop-filter: blur(18px) saturate(1.2);
  border-bottom: 1px solid var(--glass-border);
}
.nav-brand-text {
  color: var(--fg);
  font-family: 'Instrument Sans', sans-serif;
  font-weight: 700;
  font-size: 15px;
  letter-spacing: -0.01em;
}
.nav-links { display: flex; gap: 22px; flex-wrap: wrap; }
.nav-links a, .nav-right a {
  color: var(--fg-70); text-decoration: none; font-size: 0.92rem;
  transition: color 0.15s;
}
.nav-links a:hover, .nav-right a:hover { color: var(--brand); }
.nav-right { display: flex; gap: 14px; align-items: center; flex-shrink: 0; }
.btn-primary-pill {
  background: var(--brand); color: #fff !important;
  padding: 8px 18px; border-radius: 999px; font-weight: 600;
  transition: box-shadow 0.2s, transform 0.15s, background 0.15s;
  text-decoration: none;
  font-size: 0.9rem;
}
.btn-primary-pill:hover {
  background: #FF5C14;
  box-shadow: 0 0 20px rgba(255,77,0,0.35);
  transform: translateY(-1px);
}
@media (max-width: 720px) {
  nav.navbar { padding: 12px 16px; }
  .nav-links { display: none; }
  .wrap { padding: 88px 18px 48px; }
}
::selection { background: rgba(255,77,0,0.28); color: #fff; }
html[data-theme="light"] ::selection {
  background: rgba(255,77,0,0.22); color: #0a0a0a;
}
h1 { font-size: 2.6rem; line-height: 1.1; margin-bottom: 14px; font-weight: 600; letter-spacing: -0.04em; color: var(--fg); }
h2 { font-size: 1.45rem; margin: 44px 0 14px; font-weight: 600; letter-spacing: -0.02em; color: var(--fg); }
p, li { color: var(--fg-70); margin-bottom: 12px; }
li { margin-left: 20px; margin-bottom: 8px; }
code {
  background: var(--muted-fill); border-radius: 6px;
  padding: 1px 6px; font-size: 0.88em;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--fg);
}
a { color: var(--accent); }
a:hover { color: var(--brand); }
table.cmp { width: 100%; border-collapse: collapse; margin: 18px 0 8px; }
table.cmp th, table.cmp td {
  text-align: left; padding: 10px 12px; font-size: 0.92rem;
  border-bottom: 1px solid var(--glass-border); vertical-align: top;
}
table.cmp th { color: var(--fg); font-weight: 600; }
table.cmp td { color: var(--fg-70); }
table.cmp td.y { color: #34d399; }
html[data-theme="light"] table.cmp td.y { color: #047857; }
table.cmp td.n { color: var(--fg-50); }
.kicker {
  color: var(--brand); font-weight: 600; font-size: 0.85rem;
  letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 10px;
}
.note {
  border: 1px solid var(--card-border); border-radius: 12px;
  background: var(--surface); padding: 16px 18px; margin: 18px 0;
  color: var(--fg-70);
}
.note strong { color: var(--fg); }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin: 22px 0; }
.stat {
  border: 1px solid var(--card-border); border-radius: 12px;
  padding: 16px; background: var(--surface);
}
.stat .num { font-size: 1.6rem; font-weight: 700; color: var(--fg); }
.stat .lbl { font-size: 0.85rem; color: var(--fg-60); margin-top: 4px; }
footer {
  border-top: 1px solid var(--glass-border); margin-top: 64px;
  padding: 28px 24px; text-align: center;
  color: var(--fg-50); font-size: 0.85rem;
}
footer a { color: var(--fg-60); text-decoration: none; }
footer a:hover { color: var(--fg); }
:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 3px;
}
"""

# Theme bootstrap runs as soon as nav is parsed (body). Matches landing key.
PAGE_NAV = """
<script>
(function () {
  try {
    var t = localStorage.getItem("x402-theme");
    if (t !== "light" && t !== "dark") t = "dark";
    document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "dark");
  }
})();
</script>
<nav class="navbar">
  <div class="nav-left">
    <a href="/" style="display:flex;align-items:center;gap:10px;text-decoration:none;" aria-label="x402 validator home">
      <img class="icon" src="/static/logo-mark-512.png" alt="x402 validator" width="28" height="28" style="border-radius:6px;box-shadow:0 0 0 1px rgba(255,77,0,0.25);">
      <span class="nav-brand-text">x402 validator</span>
    </a>
  </div>
  <div class="nav-links">
    <a href="/#audit">Try It Free</a>
    <a href="/#pricing">Pricing</a>
    <a href="/vs-x402-doctor">Compare</a>
    <a href="/open">Open</a>
    <a href="/health">Status</a>
  </div>
  <div class="nav-right">
    <a href="/login">Log in</a>
    <a href="https://github.com/MSSATANASS/x402-validator-tools/issues">Contact</a>
    <a class="btn-primary-pill" href="/create-checkout-session?plan_id=pro">Get Started</a>
  </div>
</nav>
"""

PAGE_FOOTER = """
<footer>
  <div>© 2026 x402 validator · Apache-2.0 ·
    <a href="https://github.com/MSSATANASS/x402-conformance-engine" rel="noopener">engine</a> ·
    <a href="https://pypi.org/project/x402-conformance-suite/" rel="noopener">pip install</a> ·
    <a href="https://github.com/MSSATANASS">GitHub: MSSATANASS</a>
  </div>
</footer>
"""


def auth_nav_links(logged_in: bool) -> str:
    """Right-side nav links for the landing: dashboard if a session exists,
    log in / sign up otherwise."""
    if logged_in:
        return '<a href="/dashboard">My dashboard</a>'
    return '<a href="/login">Log in</a>\n    <a href="/signup">Sign up</a>'
