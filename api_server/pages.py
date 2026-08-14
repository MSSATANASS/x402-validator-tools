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
  flex-wrap: wrap;
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
.nav-links {
  display: flex; gap: 22px; flex-wrap: wrap;
  align-items: center;
}
.nav-links a, .nav-right a {
  color: var(--fg-70); text-decoration: none; font-size: 0.92rem;
  transition: color 0.15s, background 0.15s;
}
.nav-links a:hover, .nav-right a:hover { color: var(--brand); }
.nav-right { display: flex; gap: 10px; align-items: center; flex-shrink: 0; }
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
.theme-toggle {
  appearance: none;
  width: 38px; height: 38px;
  border-radius: 999px;
  border: 1px solid var(--glass-border);
  background: var(--surface);
  color: var(--fg);
  display: inline-flex; align-items: center; justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.12s;
}
.theme-toggle:hover {
  border-color: rgba(255,77,0,0.45);
  box-shadow: 0 0 0 3px rgba(255,77,0,0.10);
}
.theme-toggle:active { transform: scale(0.96); }
.theme-toggle svg { width: 18px; height: 18px; display: block; }
html[data-theme="dark"] .theme-toggle .icon-moon { display: none; }
html[data-theme="dark"] .theme-toggle .icon-sun { display: block; }
html[data-theme="light"] .theme-toggle .icon-sun { display: none; }
html[data-theme="light"] .theme-toggle .icon-moon { display: block; }
.nav-left .nav-hex {
  width: 28px; height: 28px;
  display: block;
  flex-shrink: 0;
  background: transparent !important;
  box-shadow: none !important;
  border-radius: 0 !important;
  overflow: visible;
}
.nav-left .nav-hex .hex-stroke {
  fill: none;
  stroke: var(--brand, #FF4D00);
  stroke-width: 2.6;
  stroke-linejoin: round;
  stroke-linecap: round;
}
.theme-toggle[aria-pressed="true"] {
  border-color: rgba(255,77,0,0.40);
  box-shadow: 0 0 0 3px rgba(255,77,0,0.10);
}
.nav-menu-btn {
  appearance: none;
  display: none;
  align-items: center; justify-content: center;
  width: 38px; height: 38px;
  border-radius: 999px;
  border: 1px solid var(--glass-border);
  background: var(--surface);
  color: var(--fg);
  cursor: pointer;
  flex-shrink: 0;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.nav-menu-btn:hover {
  border-color: rgba(255,77,0,0.45);
  box-shadow: 0 0 0 3px rgba(255,77,0,0.10);
}
.nav-menu-btn svg { width: 18px; height: 18px; display: block; }
.nav-menu-btn .icon-close { display: none; }
.nav-menu-btn[aria-expanded="true"] .icon-open { display: none; }
.nav-menu-btn[aria-expanded="true"] .icon-close { display: block; }
.nav-menu-btn[aria-expanded="true"] {
  border-color: rgba(255,77,0,0.45);
  box-shadow: 0 0 0 3px rgba(255,77,0,0.10);
}
@media (max-width: 720px) {
  nav.navbar { padding: 12px 16px; row-gap: 0; }
  .nav-brand-text { max-width: 40vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .nav-menu-btn { display: inline-flex; }
  .nav-right .nav-contact { display: none; }
  .nav-right .btn-primary-pill { padding: 8px 12px; font-size: 0.82rem; }
  .nav-links {
    display: none;
    order: 3;
    width: 100%;
    flex-direction: column;
    align-items: stretch;
    gap: 2px;
    margin: 10px 0 4px;
    padding: 12px 8px 8px;
    background: var(--surface);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    position: relative;
    box-shadow: 0 16px 40px -20px rgba(0,0,0,0.55);
  }
  .nav-links::before {
    content: '';
    position: absolute; top: 0; left: 14px; right: 14px; height: 2px;
    border-radius: 0 0 2px 2px;
    background: linear-gradient(to left, #2B2644, #FF4D00, #FF8A4D);
    opacity: 0.9;
  }
  nav.navbar.nav-open .nav-links { display: flex; }
  .nav-links a {
    padding: 12px 12px;
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.95rem;
  }
  .nav-links a:hover, .nav-links a:focus-visible {
    background: var(--muted-fill);
    color: var(--fg);
    outline: none;
  }
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
/* Comparison tables: card surface + mobile-safe wrap (vs page) */
.cmp-scroll {
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  margin: 18px 0 8px;
  border: 1px solid var(--card-border);
  border-radius: 12px;
  background: var(--surface);
}
table.cmp {
  width: 100%;
  min-width: 520px;
  border-collapse: collapse;
  margin: 0;
}
table.cmp th, table.cmp td {
  text-align: left; padding: 10px 12px; font-size: 0.92rem;
  border-bottom: 1px solid var(--glass-border); vertical-align: top;
}
table.cmp tr:last-child th, table.cmp tr:last-child td { border-bottom: none; }
@media (max-width: 640px) {
  table.cmp th, table.cmp td { padding: 8px 10px; font-size: 0.82rem; }
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

# Theme bootstrap + mobile menu (body). Matches landing key / nav panel pattern.
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
<nav class="navbar" id="pageNav">
  <div class="nav-left">
    <a href="/" style="display:flex;align-items:center;gap:10px;text-decoration:none;" aria-label="x402 validator home">
      <svg class="nav-hex" viewBox="0 0 40 40" width="28" height="28" aria-hidden="true" focusable="false">
        <polygon class="hex-stroke" points="20,3.5 34.5,11.75 34.5,28.25 20,36.5 5.5,28.25 5.5,11.75"/>
      </svg>
      <span class="nav-brand-text">x402 validator · evidence</span>
    </a>
  </div>
  <div class="nav-links" id="pageNavLinks">
    <a href="/#audit">Try It Free</a>
    <a href="/#pricing">Pricing</a>
    <a href="/vs-x402-doctor">Compare</a>
    <a href="/open">Open</a>
    <a href="/health">Status</a>
  </div>
  <div class="nav-right">
    <button type="button" class="theme-toggle" id="pageThemeToggle" aria-label="Switch to light mode" aria-pressed="true" title="Switch to light mode">
      <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>
      </svg>
      <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <path d="M21 14.5A8.5 8.5 0 1 1 9.5 3a7 7 0 0 0 11.5 11.5z"/>
      </svg>
    </button>
    <a href="/login">Log in</a>
    <a class="nav-contact" href="https://github.com/MSSATANASS/x402-validator-tools/issues">Contact</a>
    <a class="btn-primary-pill" href="/create-checkout-session?plan_id=pro">Get Started</a>
    <button type="button" class="nav-menu-btn" id="pageNavMenuBtn" aria-label="Open menu" aria-expanded="false" aria-controls="pageNavLinks" title="Menu">
      <svg class="icon-open" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
      <svg class="icon-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
    </button>
  </div>
</nav>
<script>
(function () {
  function applyTheme(theme) {
    var t = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("x402-theme", t); } catch (e) {}
    var metas = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < metas.length; i++) {
      metas[i].setAttribute("content", t === "dark" ? "#0c0b10" : "#FF4D00");
    }
    var toggle = document.getElementById("pageThemeToggle");
    if (toggle) {
      toggle.setAttribute("aria-pressed", t === "dark" ? "true" : "false");
      var label = t === "dark" ? "Switch to light mode" : "Switch to dark mode";
      toggle.title = label;
      toggle.setAttribute("aria-label", label);
    }
  }
  var themeBtn = document.getElementById("pageThemeToggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme") || "dark";
      applyTheme(cur === "dark" ? "light" : "dark");
    });
    applyTheme(document.documentElement.getAttribute("data-theme") || "dark");
  }

  var nav = document.getElementById("pageNav");
  var btn = document.getElementById("pageNavMenuBtn");
  var links = document.getElementById("pageNavLinks");
  if (!nav || !btn) return;
  function setOpen(open) {
    if (open) nav.classList.add("nav-open");
    else nav.classList.remove("nav-open");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    btn.title = open ? "Close menu" : "Menu";
  }
  btn.addEventListener("click", function () {
    setOpen(!nav.classList.contains("nav-open"));
  });
  if (links) {
    links.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () { setOpen(false); });
    });
  }
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") setOpen(false);
  });
  document.addEventListener("click", function (ev) {
    if (!nav.classList.contains("nav-open")) return;
    if (nav.contains(ev.target)) return;
    setOpen(false);
  });
  window.addEventListener("resize", function () {
    if (window.innerWidth > 720) setOpen(false);
  });
})();
</script>
"""

PAGE_FOOTER = """
<footer>
  <div>© 2026 x402 validator · built by Gael Leonardo Chulim Gongora · mss_ali / Ali Nain · Apache-2.0 ·
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
