# x402 Landing — Protocol Ledger Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Elevate `https://x402-validator-tools.onrender.com/` from “good SaaS landing” to a **distinctive, professional protocol-product page** whose visual language is unmistakably x402 (HTTP 402 / settlement / agent payments), without breaking locked landing tests or Stripe/validate behavior.

**Architecture:** Keep the landing as server-rendered HTML inside `api_server/app.py` (`_LANDING_HTML` string) — same pattern the repo already uses. All visual/functional work is CSS + markup + progressive-enhancement JS in that string; secondary pages continue to share chrome via `api_server/pages.py` only where footer/nav tokens must stay consistent. No new frontend framework.

**Tech Stack:** FastAPI · inline HTML/CSS/JS in `api_server/app.py` · pytest + TestClient · brand tokens `#FF4D00` / `#2B2644` / `#F5F5F5` · Instrument Sans + Instrument Serif · deploy via push to `main` (Render auto).

## Global Constraints

- **Do not** change `/validate`, `/create-checkout-session`, `/stripe-webhook` business logic.
- **Do not** remove or reword strings locked by `tests/test_api_server.py::TestLanding` (see Appendix A).
- **Preserve** brand markers: `#F5F5F5`, `#2B2644`, `rgba(255,77,0,0.22)`, `rgba(16,185,129,0.28)`, `hero-video-wrap`, `marquee-track`, `hcard`, `Meet the engine.`, nine checks language.
- **No** video, HLS, Unsplash, or new external media CDNs.
- **Respect** `prefers-reduced-motion`.
- **Commits:** per-invocation identity only (`Gael Leonardo Chulim Gongora` / `mss_ali@users.noreply.github.com`); never mutate `git config`.
- **Push/deploy only** when the human explicitly asks.
- Working tree already has an **uncommitted** polish pass (trust bar, how-it-works, sample chips, copy JSON, skip-link, footer cleanup). Tasks below **absorb and refine** that work rather than discard it.

---

## Design Direction (frontend-design skill)

### Subject / audience / single job

| | |
|--|--|
| **Product** | Hosted x402 strict-v2 conformance auditor (API + free demo) |
| **Audience** | Merchant operators + agent builders on Base / Coinbase x402 |
| **Page job** | Get a developer to run one free audit and trust Pro enough to buy a key |

### Rejected defaults (self-critique)

| Generic AI default | Why we skip it |
|--------------------|----------------|
| Cream ground + terracotta serif | Wrong for payment-protocol infra |
| Near-black + acid green terminal cosplay | Clashes with existing light brand; fights locked light theme tests |
| Broadsheet hairline newspaper | Unrelated to HTTP settlement vernacular |

### Token system (named)

| Token | Hex / value | Role |
|-------|-------------|------|
| `surface` | `#F5F5F5` | Page ground (locked) |
| `ink` | `#2B2644` | Brand purple-ink (locked) |
| `brand` | `#FF4D00` | Payment-required orange (locked) |
| `brand-soft` | `#FF8A4D` | Gradient end / soft accent |
| `pass` | `#10B981` / text `#047857` | Settlement OK |
| `fail` | `#EF4444` / text `#b91c1c` | Settlement reject |
| `display` | Instrument Serif | Editorial pre-headline only |
| `body` | Instrument Sans | UI + copy |
| `data` | `ui-monospace` stack | Check names, URLs, latency, amounts |

### Layout concept

```
┌─ glass nav (scroll elevation) ─────────────────────────┐
│ logo · Try Free · How · Pricing · Compare · FAQ · Status │
└────────────────────────────────────────────────────────┘
┌─ HERO (thesis) ────────────────────────────────────────┐
│  [ x402-validator-tools ]                              │
│  Ship x402 endpoints with confidence                   │
│  Audit x402 in Seconds          ← locked headline      │
│  [Get API Key] [Try It Free]                           │
│  marquee of check names                                │
└────────────────────────────────────────────────────────┘
┌─ TRUST STRIP (4 metrics) ──────────────────────────────┐
│  9 checks | ~580ms | 3/day free | Apache-2.0           │
└────────────────────────────────────────────────────────┘
┌─ HOW IT WORKS (3 cards) ───────────────────────────────┐
│  1 Paste URL → 2 Nine checks → 3 Actionable JSON       │
└────────────────────────────────────────────────────────┘
┌─ MEET THE ENGINE (existing hcards) ────────────────────┐
┌─ STACK MARQUEE ────────────────────────────────────────┐
┌─ AUDIT DEMO ★ signature ───────────────────────────────┐
│  chips · form · results as "settlement receipt"        │
│  overall badge · latency · Copy JSON · check rows      │
└────────────────────────────────────────────────────────┘
┌─ PRICING · FAQ · FOOTER ───────────────────────────────┘
```

### Signature element (one bold risk)

**“Settlement receipt” audit results** — not a generic checklist card deck. After `/audit-public` returns, the results panel looks like a **protocol receipt**:

- Header row: overall state + method-ish framing (`overall` + latency ms) in monospace meta
- Body: ordered check lines with left rail status (PASS/FAIL/ERROR/CRITICAL_FAIL) as **verdict stamps**
- Footer: remaining free audits + Pro CTA + **Copy JSON** (raw payload = the machine-readable receipt)

This is grounded in x402’s own world (402 challenge → settle → 200), not in “SaaS feature grid #47”. Numbered how-it-works **is** appropriate here because the content is a real sequential process.

### Already landed (working tree; Task 1 validates)

- Skip link, navbar `scrolled`, `theme-color`
- Trust bar, how-it-works section, sample chips
- Copy JSON, CRITICAL_FAIL/ERROR styles, footer cleanup

---

## File Map

| File | Responsibility |
|------|----------------|
| `api_server/app.py` | `_LANDING_HTML` only for this plan (~lines 269–2100 region); no API handler changes |
| `tests/test_api_server.py` | Extend `TestLanding` with new markers; keep existing assertions |
| `api_server/pages.py` | **Optional Task 6 only** if secondary pages need matching footer truth (“Postgres keystore…”) |
| `docs/superpowers/plans/2026-08-09-x402-landing-ledger-polish.md` | Copy of this plan into repo on execute (first commit step) |

---

### Task 1: Lock the new structure with tests (TDD)

**Files:**
- Modify: `tests/test_api_server.py` (`TestLanding.test_renders_html` and/or new methods)
- Modify: `api_server/app.py` only if tests fail on missing markup (then add minimal markers)

**Interfaces:**
- Consumes: existing `TestClient` fixture `client` from `tests/test_api_server.py`
- Produces: assertions that future tasks must keep green

- [ ] **Step 1: Write failing assertions for new sections**

Append to `TestLanding` (new method preferred so the giant test stays readable):

```python
def test_landing_protocol_polish_markers(self, client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    # a11y + chrome
    assert 'class="skip-link"' in r.text
    assert 'id="topNav"' in r.text or 'id="topNav"' in r.text
    assert 'name="theme-color"' in r.text
    # structure
    assert 'class="trust-bar"' in r.text or 'class="trust-bar"' in r.text
    assert 'id="how"' in r.text
    assert "From URL to verdict in three steps" in r.text
    # demo UX
    assert 'class="sample-chips"' in r.text
    assert "Copy JSON" in r.text or "copyAuditJson" in r.text
    assert "btn-copy-json" in r.text
    # settlement-receipt framing (Task 3 may introduce class name)
    assert "operator-actionable errors" in r.text  # still locked subhead
    # footer honesty (no duplicate Issues; no api_keys.json claim)
    assert r.text.count("GitHub Issues") <= 2
    assert "api_keys.json" not in r.text
    assert "Postgres keystore" in r.text or "Neon" in r.text or "hosted on Render" in r.text
```

Note: If current uncommitted HTML already satisfies most of these, the test may pass immediately — that is OK; treat as characterization test.

- [ ] **Step 2: Run the new test**

```powershell
cd C:\Users\g_leo\Projects\x402\x402-validator-tools
.\.venv\Scripts\python.exe -m pytest tests/test_api_server.py::TestLanding -q --tb=short
```

Expected: all `TestLanding` tests green (or only the new ones red if markers missing).

- [ ] **Step 3: If red, add only the missing markers in `_LANDING_HTML`**

Do not redesign yet — just restore trust-bar / how / chips / copy JSON if accidentally deleted.

- [ ] **Step 4: Re-run TestLanding**

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_api_server.py api_server/app.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "test(landing): lock protocol polish structure markers"
```

---

### Task 2: Settlement-receipt results UI (signature)

**Files:**
- Modify: `api_server/app.py` (`_LANDING_HTML` CSS for `.audit-results` / `.receipt-*` and JS `renderResults`)
- Modify: `tests/test_api_server.py` (assert receipt class present in HTML/JS string)

**Interfaces:**
- Consumes: existing `fetch('/audit-public', …)` payload shape `{url, overall, summary, checks:[{name,status,message}], latency_ms, remaining_today?}`
- Produces: DOM structure with classes `receipt`, `receipt-meta`, `receipt-row`, `btn-copy-json`; keeps `check-row` / `status-PASS` for any existing CSS assumptions

- [ ] **Step 1: Failing test for receipt markup in page source**

```python
def test_landing_audit_results_use_receipt_framing(self, client: TestClient) -> None:
    r = client.get("/")
    assert "receipt" in r.text  # class receipt or receipt-meta
    assert "btn-copy-json" in r.text
    assert "__x402LastAudit" in r.text
```

- [ ] **Step 2: Run test — expect FAIL if class names not yet present**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_server.py::TestLanding::test_landing_audit_results_use_receipt_framing -v
```

- [ ] **Step 3: Implement receipt framing in CSS + `renderResults`**

CSS additions inside `_LANDING_HTML` `<style>` (keep existing `.check-row` rules; wrap/enhance):

```css
.audit-results.receipt {
  border: 1px solid rgba(10,10,10,0.10);
  border-radius: 16px;
  background: #fff;
  box-shadow: var(--shadow-md);
  padding: 16px 16px 8px;
  position: relative;
}
.audit-results.receipt::before {
  content: "SETTLEMENT RECEIPT";
  display: block;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.68rem;
  letter-spacing: 0.14em;
  color: var(--fg-50);
  margin: 0 0 10px 2px;
}
.receipt-meta {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  color: var(--fg-50);
  margin: 0 0 12px;
}
```

JS: when showing results, set `results.className = 'audit-results receipt'` (and clear on loading). Keep badge/check-row markup. Keep Copy JSON button.

- [ ] **Step 4: Run TestLanding full class**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_server.py::TestLanding -q --tb=short
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add api_server/app.py tests/test_api_server.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "feat(landing): settlement-receipt framing for audit results"
```

---

### Task 3: Hero + trust polish (still light theme)

**Files:**
- Modify: `api_server/app.py` CSS/HTML for hero subcopy spacing, trust-bar overlap, CTA hierarchy only
- Test: existing TestLanding + optional assert for trust labels

**Interfaces:**
- Consumes: locked strings `Ship x402 endpoints with confidence`, `Audit x402 in Seconds`, `Get Your API Key`, `Try It Free`
- Produces: unchanged locked strings; refined CSS only

- [ ] **Step 1: Characterization test (must stay green)**

```python
def test_landing_hero_locked_copy(self, client: TestClient) -> None:
    t = client.get("/").text
    assert "Ship x402 endpoints with confidence" in t
    assert "Audit x402 in Seconds" in t
    assert "Get Your API Key" in t
    assert 'href="#audit">Try It Free</a>' in t
```

- [ ] **Step 2: Run — expect PASS**

- [ ] **Step 3: CSS-only refinements**

In `_LANDING_HTML` style block:

1. Slightly tighten hero `min-height` on large screens if content feels sparse (`min-height: min(720px, calc(100vh - 120px))` or keep current if tests rely on nothing about height).
2. Ensure `.trust-bar` sits cleanly under hero (`margin-top: -36px` max; avoid covering CTAs on mobile — use `@media (max-width: 640px) { .trust-bar { margin-top: 16px; } }`).
3. Make secondary CTA border use brand-soft hover without changing markup.

Do **not** change headline gradient text strings.

- [ ] **Step 4: Run TestLanding**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_server.py::TestLanding -q
```

- [ ] **Step 5: Commit**

```powershell
git add api_server/app.py tests/test_api_server.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "style(landing): tighten hero/trust composition"
```

---

### Task 4: Pricing + FAQ micro-polish

**Files:**
- Modify: `api_server/app.py` pricing/FAQ CSS + small HTML only where tests allow
- Test: assert `Most popular` still present; assert `$9` / `$49`

- [ ] **Step 1: Failing/characterization test**

```python
def test_landing_pricing_and_faq_anchors(self, client: TestClient) -> None:
    t = client.get("/").text
    assert 'id="pricing"' in t
    assert 'id="faq"' in t
    assert "Most popular" in t
    assert "$9" in t and "$49" in t
    assert "<details" in t
```

- [ ] **Step 2: Run — expect PASS**

- [ ] **Step 3: Implement polish**

1. Pricing: ensure Pro featured card remains the visual anchor; optional `aria-label="Pro plan, most popular"` on featured plan div.
2. FAQ: ensure open state uses brand border (already partially there); add `scroll-margin-top: 88px` on `#pricing`, `#faq`, `#audit`, `#how` for fixed nav.
3. Free plan CTA: keep `Start free` text if present (do not invent new locked strings).

```css
#audit, #how, #pricing, #faq {
  scroll-margin-top: 88px;
}
```

- [ ] **Step 4: Run TestLanding**

- [ ] **Step 5: Commit**

```powershell
git add api_server/app.py tests/test_api_server.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "style(landing): pricing/FAQ scroll and focus polish"
```

---

### Task 5: Manual visual QA + full suite gate

**Files:**
- None required (or tiny CSS fixes if bugs found)

- [ ] **Step 1: Run full landing-related automated suite**

```powershell
cd C:\Users\g_leo\Projects\x402\x402-validator-tools
.\.venv\Scripts\python.exe -m pytest tests/test_api_server.py::TestLanding tests/test_api_server.py::TestPlans -q --tb=short
```

Expected: all PASS.

- [ ] **Step 2: Local visual smoke**

```powershell
.\.venv\Scripts\python.exe -m uvicorn api_server.app:app --reload --port 8000
```

Open `http://127.0.0.1:8000/`. Checklist:

1. Hero headline + CTAs readable; marquee moves; reduced-motion OK if toggled in OS.
2. Trust bar not covering CTAs on 375px width.
3. How-it-works three cards stack on mobile.
4. Audit form: chip fills URL; submit shows loading; mock or live `/audit-public` shows receipt + Copy JSON.
5. Navbar gains shadow after scroll; Status/Pricing/FAQ anchors land below nav.
6. Footer has no `api_keys.json` claim; Issues link once.

- [ ] **Step 3: Fix only regressions found**

If mobile trust-bar overlaps, adjust CSS; re-run TestLanding.

- [ ] **Step 4: Full suite (optional but recommended before push)**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q --tb=line
```

Expected: green (or only known DB skips).

- [ ] **Step 5: Commit only if fixes landed; else skip**

```powershell
git status -sb
# if dirty:
git add api_server/app.py tests/test_api_server.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "fix(landing): mobile trust-bar / QA follow-ups"
```

---

### Task 6 (optional): Align secondary page footer claim

**Files:**
- Modify: `api_server/pages.py` **only if** `PAGE_FOOTER` still says something false about key storage
- Test: `tests/test_api_server.py::test_vs_doctor_page` / `test_open_page` must stay green

- [ ] **Step 1: Grep secondary chrome**

```powershell
Select-String -Path api_server\pages.py -Pattern "api_keys|footer|Postgres|Neon"
```

- [ ] **Step 2: If outdated claim found, update `PAGE_FOOTER` to match landing honesty**

Keep support links; do not add new pages.

- [ ] **Step 3: Run**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api_server.py::TestLanding::test_vs_doctor_page tests/test_api_server.py::TestLanding::test_open_page -q
```

- [ ] **Step 4: Commit if changed**

```powershell
git add api_server/pages.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "docs(ui): align secondary footer with Postgres keystore wording"
```

---

### Task 7: Ship (human-gated)

**Files:** none (git only)

- [ ] **Step 1: Show status to human**

```powershell
git status -sb
git log origin/main..HEAD --oneline
```

- [ ] **Step 2: Push only after explicit human OK**

```powershell
git push origin main
```

- [ ] **Step 3: Verify production after deploy**

```powershell
# wait for Render deploy, then:
(Invoke-WebRequest https://x402-validator-tools.onrender.com/ -UseBasicParsing).StatusCode
# expect 200; spot-check trust-bar / how / receipt in browser
```

---

## Appendix A — Locked TestLanding strings (must survive)

From `tests/test_api_server.py::TestLanding.test_renders_html` (non-exhaustive but critical):

- `Ship x402 endpoints with confidence`
- `Audit x402 in Seconds`
- `operator-actionable errors`
- `$9`, `$49`
- `/create-checkout-session?plan_id=pro|enterprise|free`
- `Instrument+Sans`, `Instrument+Serif`
- `#F5F5F5`, `#2B2644`, `rgba(10,10,10,0.70)`
- `marquee-track`, `stack-track`, `@keyframes marquee`, `hcard`, `Meet the engine.`
- `hero-video-wrap`, no `<video`, no `hls.js` / `stream.mux.com` / unsplash
- `rgba(255,77,0,0.22)`, `rgba(16,185,129,0.28)`, `rgba(255,77,0,0.16)`, `rgba(16,185,129,0.20)`
- `hero-video-wrap::after`, `hero-grid`, `hero-flow`, `@keyframes settle`, `flow-nodes`
- `blur(120px)`, `mix-blend-mode: multiply`, `prefers-reduced-motion`
- `Get Your API Key`, `Try It Free`, `@keyframes fadeUp`, `@keyframes scaleIn`, `Most popular`
- `id="auditForm"`, `id="auditResults"`, `fillUrl`, `/audit-public`
- `3 audits per IP per day` OR `3/IP/day`
- `id="faq"`, `<details`, `x402 conformance`
- JSON-LD `SoftwareApplication` + `FAQPage`, `Gael L Chulim`
- OG/twitter/canonical/favicon/robots
- `/static/logo-mark-512.png`, `/static/logo-wordmark.png`, `brand-eyebrow`, `x402-validator-tools`
- No `#stories`, no `/docs`, no `Book A Demo`, no `Customer Stories`
- `href="#audit">Try It Free</a>`, `href="#faq">FAQ</a>`, `href="/health">Status</a>`
- `We run nine checks against it`, `all nine checks`, `9 checks`
- `directory_cold_probe`, `batch_settlement_requirements`
- Links: `/vs-x402-doctor`, `/open`

## Appendix B — Out of scope

- Dark mode theme flip
- Extracting `_LANDING_HTML` to a Jinja template (refactor only if app.py edits become unmanageable mid-task — prefer not)
- Rate-limit / compensation product work (facilitator_policy)
- Windows-Use agent integration on the marketing page
- Stripe pricing changes

## Self-Review

1. **Spec coverage:** Aesthetic + functional professional polish → Tasks 2–4; tests → Task 1; QA → Task 5; ship → Task 7; secondary chrome honesty → Task 6 optional.
2. **Placeholders:** None; steps include concrete CSS/JS/test snippets.
3. **Consistency:** Receipt classes `receipt` / `receipt-meta` / `btn-copy-json` / `__x402LastAudit` aligned across Task 1–2.
4. **Uniqueness check (frontend-design):** Signature is settlement-receipt results + x402 check vernacular, not cream-serif template or acid-terminal cosplay; brand orange/ink/light locked by product history and tests.

---

## Execution Handoff

Plan complete (session plan file). On execute, also save a copy to:

`docs/superpowers/plans/2026-08-09-x402-landing-ledger-polish.md`

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — this session, `executing-plans`, batch with checkpoints  

**Which approach?** After approval, start at Task 1 (tests first).
