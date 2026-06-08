# Schild Inc B2B Prospect Engine — Project Memory

Last updated: 2026-06-02

This file is read automatically by Claude Code when starting a new session
in this repo. It captures architecture, history, gotchas, and pending work
so we don't re-explain from scratch each time.

---

## What this is

A B2B outreach engine for **Schild Inc** (Dutch metal-label manufacturer
targeting bicycle shops + 10 other sectors across NL/DE/FR/BE/UK/US/etc.).

- **Production**: https://schild-prospect-engine-production.up.railway.app
- **Auth**: `schild` / `Schildinc#01` (HTTP Basic on every page)
- **GitHub**: https://github.com/kevinolla/Schildinc.git (main = trunk, NO auto-deploy — must `railway up`)
- **Owner email**: schild.inc.official@gmail.com
- **Stack**: FastAPI + SQLAlchemy + Alembic + Postgres on Railway, Jinja2 templates, vanilla JS

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  Railway container                       │
│  ┌─────────────────────────────────────────────────┐    │
│  │  FastAPI app (app/main.py)                      │    │
│  │  - Pages: /kvk, /customers, /leads, /prospects, │    │
│  │    /queue, /suppression, /logs, /customers/analytics │
│  │  - Agent API:   /api/kvk/agent/{pending,result} │    │
│  │  - Webform API: /api/leads/webform (CORS-open)  │    │
│  │  - Exports: /kvk/export.csv, /customers/export  │    │
│  ├─────────────────────────────────────────────────┤    │
│  │  Background daemons (lifespan startup)          │    │
│  │  - KVK auto-enrich  (3 workers, every 30s)      │    │
│  │  - FB-sheet sync    (every 15 min)              │    │
│  │  - Lead classifier  (every 60s, NEW)            │    │
│  └─────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
              ↑                       ↓
              │ HTTP                  │ Postgres
              │                       ↓
   ┌──────────────────┐    ┌──────────────────┐
   │ Local Playwright │    │ Railway Postgres │
   │ agent (laptop)   │    │ (3990 KVK,       │
   │ scripts/email_   │    │ ~1.1M leads*,    │
   │ agent.py         │    │ 3256 customers)  │
   └──────────────────┘    └──────────────────┘
   *fb_lead_id is sparse — actual row count ~9k.
   The high `id` numbers are an auto-increment sequence quirk.
```

The **local agent** runs on the user's Mac because Google returns JS-only
stub pages to cloud IPs. Residential IP → real rendered DOM with snippets.

---

## Tables (live row counts as of 2026-06-02)

| Table | Rows | Purpose |
|---|---|---|
| `customers`        | 3,256 | Paying customers (Stripe + Schild historical CSV) |
| `kvk_companies`    | 3,990 | Dutch Chamber of Commerce — outreach pool |
| `facebook_leads`   | ~8,500 unique fb_lead_ids | Lead Ads sheet (auto-synced) + 50k-row historical CSV |
| `prospects`        |    68 | Earlier Google Places imports |
| `invoices`         |   ~7k | Linked to customers |

---

## Sector taxonomy

11 canonical sectors used by **both** Customer.main_sector and
FacebookLead.main_sector (defined in `app/lead_classifier.py:SECTORS`):

```
Bike · Candles · Woodwork · Furniture · SteelWork · Music · Fashion ·
Liquor & Bottles · Service · Art · Uncategorized
```

Live distribution on leads as of 2026-06-02: Bike 2,759 · Art 2,533 ·
Uncategorized 1,217 · Service 800ish · SteelWork 412 · others.

---

## Key modules

| Path | Role |
|---|---|
| `app/main.py` | All HTTP routes (~2,500 lines). Single file. |
| `app/models.py` | SQLAlchemy models — Customer, KvkCompany, Prospect, FacebookLead, Invoice, SuppressionEntry, etc. |
| `app/matching.py` | **STRICT** KVK↔Customer matching: exact email OR exact (name + country). Fuzzy/domain matching deliberately removed. |
| `app/kvk_enrichment.py` | KVK auto-enrich scheduler (3 workers, 6s Playwright timeout). Stages: Places → Playwright crawl → MX-guess `info@domain` |
| `app/discovery.py` | Core Playwright scraper |
| `app/email_guesser.py` | MX-validated `info@<domain>` pattern fallback (skips free webmail) |
| `app/brave_search.py` | Brave Search API. Circuit-breaker after 5 consecutive 402s. |
| `app/bing_search.py` | HTML scrape — cloud IPs get SPA stub, kept for completeness |
| `app/playwright_search.py` | Real-Chromium Google scraper (works from cloud, slow ~5-8s). Module-level `_LAUNCH_LOCK` serializes launches. |
| `app/facebook_leads.py` | FB sheet auto-sync **+** classifier daemon (lines `start_lead_classifier_scheduler`, `classify_pending_leads`) |
| `app/lead_classifier.py` | **Keyword sector classifier** — fast regex matcher. 11 sectors, NL/DE/FR/EN keywords. ~10k rows/sec. |
| `app/customer_normalizer.py` | Schild historical customer CSV importer (3,078 order-lines → 2,052 customers) |
| `app/country_codes.py` | ISO-2 ↔ name registry. `to_iso2()` canonicalizes any input. |
| `app/klaviyo_sync.py` | Klaviyo v3 profile push (list `XHgkXM`) |
| `scripts/email_agent.py` | **Local browser agent** — residential IP, hands-free, multi-channel extractor |
| `scripts/install-agent-daemon.sh` + `.plist` | macOS launchd installer (always-on agent) |
| `app/gmail_sender.py` | **Gmail OAuth + send** — refresh token stored in `gmail_accounts` table (Railway FS is ephemeral). Sends via Gmail API `users.messages.send` with send-as alias. |
| `app/email_engine.py` | **Email campaign engine** — merge fields, open-pixel + click-rewrite + unsubscribe injection, recipient build from KVK/leads/customers, throttled send loop + background daemon. |
| `app/email_library.py` | **5 starter templates** (English, bike) — cold intro, warm intro, cold follow-up, warm follow-up, VIP. Idempotent seeding via `seed_starter_templates()`; bump `STARTER_SEED_VERSION` to re-seed. |
| `app/emailing.py` | OLDER prospect-centric outreach (Resend/SMTP, Dutch templates, /queue). Separate from the new Gmail engine. |
| `app/contacts.py` | **CRM Contact Hub** — strict identity resolution (merge on exact email/phone/name+country), idempotent backfill from customers+KVK+leads+prospects, unified timeline. Page `/contacts`. See `CRM_INHOUSE_BUILD_SPEC.md`. |
| `app/inbox.py` | **CRM Shared Inbox** — conversation/message logic, assignment, statuses, canned replies, seeding. Page `/inbox` (3-pane Trengo-style). |
| `app/gmail_inbound.py` | **Two-way email** — polls connected Gmail for replies (needs `gmail.readonly` scope), threads into conversations, auto-creates contacts for unknown senders. Background daemon `start_gmail_inbound_scheduler()`. |
| `app/whatsapp.py` | **WhatsApp Business Cloud API** (direct Meta) — Graph API send text/template, webhook verify + X-Hub-Signature-256, inbound threading into inbox, 24h service-window check. Routes `GET/POST /webhooks/whatsapp`. Needs `WHATSAPP_*` env vars. |
| `app/instagram.py` | **Instagram Messaging** (official Meta) — inbound DMs into inbox + replies within 24h window only (NO cold DMs via API). Webhook `GET/POST /webhooks/instagram`. Reuses conversations/messages (channel='instagram'). Needs `INSTAGRAM_*` env vars. LinkedIn cold = manual helper on `/contacts/{id}` (no automation — ToS/ban safe). Cold pool = `/contacts?cold=1` (KVK+Maps, excludes customers+form leads). |
| `app/auth.py` | **Agent login + roles** (Phase 6) — PBKDF2 passwords, signed session cookie, `current_agent`/`is_admin`/`require_admin_role`. Layered on HTTP Basic: owner=admin, teammates role-limited. `/login` `/logout`. |
| `app/reporting.py` | **Reports** — email/inbox/contacts rollups + per-agent + avg first-response. Page `/reports`. `live_counts()` powers the SSE badge. |
| `app/audit.py` | **Audit log** — `log_audit()` on sensitive actions; admin-only `/audit` view. |

---

## Email engine (Gmail-backed campaigns) — added 2026-06-02

A full CRM email system at **`/emails`** (nav: "Email Campaigns"), separate
from the older `/queue` prospect outreach.

- **Send transport**: Gmail API (free tier). OAuth "Web app" client →
  `GMAIL_CLIENT_ID`/`GMAIL_CLIENT_SECRET`. Refresh token stored in
  `gmail_accounts` table. Redirect URI = `{APP_BASE_URL}/emails/gmail/callback`
  (must be registered in Google Cloud Console).
- **Send-as alias**: `GMAIL_SEND_AS` (e.g. `sales@schildinc.com`) MUST be a
  *verified* "Send mail as" alias on the authorized Gmail account — a plain
  forwarding address is NOT enough (Gmail rejects the From header).
- **Audiences**: KVK companies (excludes already-clients + no-email), FB/web
  leads, existing customers — filter by tier/sector/country or pass explicit
  `?ids=` from a list page.
- **Tracking**: open pixel `GET /e/o/{token}.gif`, click redirect
  `GET /e/c/{token}?u=`, unsubscribe `GET|POST /e/u/{token}` (RFC 8058
  one-click + adds a `SuppressionEntry`). These 3 endpoints are PUBLIC (no auth).
- **Throttling**: `GMAIL_DAILY_LIMIT` (default 80 — gradual warm-up; consumer
  cap ~500), `GMAIL_SEND_SPACING_SECONDS` (default 8s). Background daemon
  `start_email_sender_scheduler()` drains `sending` + due `scheduled` campaigns
  one-at-a-time, never all at once. Ramp the limit up weekly as reputation builds.
- **Templates**: 5 starter templates seeded on startup; operator can add/edit
  custom ones at `/emails/templates`. Merge fields:
  `{{company_name}} {{contact_name}} {{city}} {{country}} {{website}}
  {{sender_name}} {{reply_to}} {{unsubscribe_url}}`.
- Suppression is re-checked at send time, so an unsubscribe mid-campaign is honored.

## Migrations (current head: 0013)

| Rev | Adds |
|---|---|
| 0001 | Initial schema |
| 0002 | Prospect discovery + tiering fields |
| 0003 | Contact channels on prospects |
| 0004 | Discovery lists |
| 0005 | KVK tables |
| 0006 | KVK fields on prospects |
| 0007 | KVK social columns (whatsapp_*, instagram_url, linkedin_url) |
| 0008 | facebook_leads table |
| 0009 | FB lead sales annotations (quality_score, progress, pic, …) |
| 0010 | Customer rich fields (main_sector, sub_sector, customer_segment, contact_person, phone_primary, website) |
| 0011 | KVK `search_attempts` counter + index |
| 0012 | **`facebook_leads.main_sector` + `sub_sector` + `classifier_version`** |
| 0013 | **Email engine**: `email_templates`, `email_campaigns`, `email_campaign_recipients`, `email_events`, `gmail_accounts` |
| 0014 | **CRM Contact Hub**: `contacts`, `contact_channels`, `activities` |
| 0015 | **CRM Shared Inbox**: `agents`, `conversations`, `messages`, `canned_replies` + `gmail_accounts.last_poll_at` |
| 0016 | **CRM WhatsApp**: `whatsapp_templates` (send/receive reuses conversations/messages, channel='whatsapp') |
| 0017 | **CRM roles + audit**: `agents.password_hash`/`last_login_at`, `audit_logs` |

Alembic runs on container startup (`alembic upgrade head` in start command).

---

## Important architectural decisions / gotchas

### KVK matching is STRICT
ONLY two ways to be flagged Klant (existing customer):
1. Exact lowercased email match
2. Exact `canonical_company_name_clean` + uppercased country match

**Domain matching and fuzzy name matching are DELIBERATELY REMOVED.** Don't reintroduce.

### Lead sector classifier
- Pure regex keyword matching — no LLM, no API. ~10k rows/sec.
- Vocabulary in `app/lead_classifier.py:SECTOR_KEYWORDS` — mixed NL/DE/FR/EN
- `classifier_version` column on facebook_leads — bump `CURRENT_CLASSIFIER_VERSION` in `app/facebook_leads.py` when keywords change to force re-classification
- Daemon: every 60s, picks up rows with `classifier_version < CURRENT` and classifies in batches of 2,000
- Inline: webform endpoint classifies on the spot before save

### Agent endpoint pagination
`/api/kvk/agent/pending`:
- Filters out `discovered`/`partial`/`no_contacts` records
- Filters out `already_client_flag = True`
- Filters `search_attempts < max_attempts` (default 2)
- Orders by `search_attempts ASC, id ASC` (never-searched first)

`/api/kvk/agent/result` ALWAYS increments `search_attempts += 1`.

### Local agent (scripts/email_agent.py) is hands-free
No prompts EVER. CAPTCHA → log + `wait_for_timeout(90s)` + skip + move on. Record stays in `/agent/pending` for later.

### Webform ingest — `POST /api/leads/webform`
- CORS-open (any origin can POST)
- Accepts JSON OR form-encoded
- Required: at least one of email/phone/company_name
- Auto-classifies sector inline
- Creates `fb_lead_id = webform:{source_site}:{source_form}:{email}` (dedupable)
- Re-runs match classification against customers + KVK

### Country code/name mismatch between tables
- `customers.country_code` = ISO-2 (`NL`, `DE`, `FR`)
- `facebook_leads.country` = full uppercase names (`NETHERLAND`, `GERMANY`, `FRANCE`, `USA`)
- When combining for exports, map: `FR↔FRANCE`, `DE↔GERMANY`, `NL↔NETHERLAND/NETHERLANDS`

### KVK enrichment scheduler stability
3 workers, 6s Playwright timeout, periodic stuck-record cleanup every batch. Spawned via `start_auto_enrichment_scheduler()` in `app/kvk_enrichment.py`. Module-level `_scheduler_started` flag = idempotent.

### Playwright threading
`sync_playwright()` from multiple threads = `RuntimeError: Racing with another loop`. Fixed by module-level `threading.Lock()` in `app/playwright_search.py`.

### Brave Search circuit breaker
After 5 consecutive 402 Payment Required, `is_enabled()` returns False for the rest of the UTC day. State in `_breaker_state` dict.

### Klaviyo
- Key: `KLAVIYO_PRIVATE_API_KEY` env var
- Target list: `XHgkXM` ("KVK Lead List")
- v3 API, revision `2024-02-15`
- Push endpoint: `POST /kvk/push-klaviyo`

### Google Sheet auto-sync (FB leads)
- Sheet ID: `10k2UB3qefKvskF1YemikhVCPk0JI8xmScH2dj_I7h5g`, gid `1219149797`
- Public CSV export URL (no OAuth)
- Polled every 15 min by `_fb_sync_loop()`
- Importer uses Postgres `ON CONFLICT (fb_lead_id) DO UPDATE`

### FastAPI route ordering gotcha
**Static-path GET routes MUST be declared BEFORE catch-all `/{int_param}` routes.** Otherwise FastAPI tries to parse the path segment as an int and returns 422.
Example fix: `/kvk/export.csv` MUST be above `/kvk/{company_id}`.

---

## Environment variables (Railway)

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Postgres (auto-injected) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Basic auth credentials |
| `GOOGLE_PLACES_API_KEY` | Places API |
| `BRAVE_API_KEY` | Brave Search (out of credit) |
| `BRAVE_DAILY_LIMIT` | Default 300 |
| `KLAVIYO_PRIVATE_API_KEY` | Klaviyo |
| `KLAVIYO_LIST_ID` | `XHgkXM` |
| `KVK_AUTO_ENRICH_ENABLED` | `true` |
| `KVK_AUTO_ENRICH_BATCH` | Default 12 |
| `KVK_AUTO_ENRICH_INTERVAL` | Default 30 |
| `KVK_AUTO_ENRICH_WORKERS` | Default 3 |
| `PLAYWRIGHT_TIMEOUT_MS` | Default 6000 |
| `FB_LEADS_AUTO_SYNC_ENABLED` | `true` |
| `FB_LEADS_AUTO_SYNC_INTERVAL` | Default 900 (15 min) |
| `FB_LEADS_CLASSIFIER_ENABLED` | `true` (NEW) |
| `FB_LEADS_CLASSIFIER_INTERVAL` | Default 60s (NEW) |

---

## How to develop / deploy

### Local dev
```bash
cd "/Users/kevinolla/AI Project/B2B Prospect tool"
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

### Deploy to Railway
```bash
cd "/Users/kevinolla/AI Project/B2B Prospect tool"
railway login   # if token expired (every few weeks)
railway up --service schild-prospect-engine
```
~10 min build (nixpacks reinstalls Playwright + Chromium every time).

### Run the local agent (manual)
```bash
python scripts/email_agent.py                          # default
python scripts/email_agent.py --headless --max 100     # quick test
python scripts/email_agent.py --debug                  # explain misses
```

### Install always-on agent (launchd)
```bash
bash scripts/install-agent-daemon.sh
```
Starts immediately + at every login. Logs at `~/Library/Logs/schild-kvk-agent.log`.

---

## Production DB access (read-only debug)

```python
from sqlalchemy import create_engine, text
e = create_engine('postgresql+psycopg://postgres:LrTsgCYOvlJPvbcWgpqWUGycnyYUjYLq@switchyard.proxy.rlwy.net:13263/railway')
with e.connect() as c:
    print(c.execute(text("SELECT main_sector, COUNT(*) FROM facebook_leads GROUP BY 1 ORDER BY 2 DESC")).fetchall())
```

**Don't commit this connection string** — rotates if Railway regenerates DB creds.

---

## Export endpoints + recipes

### `/customers/export.csv`
Filters: `sector`, `country` (multi-value: repeat `country=NL&country=DE`), `segment`, `search`, `sort`. Filename auto-reflects active filters.

### `/kvk/export.csv`
Filters: `tier`, `has_email` (1/0), `match`, `confidence`. Treats `all`/`any`/empty as no-op.

### Combined Customer+Lead exports (one-off Python)
Use the recipe at the bottom of `scripts/` history — query both tables, UNION ALL with a `source` column, map country names. Pattern documented at top of the export functions:

```python
LEAD_COUNTRY_NAMES = {'FR': ['FRANCE'], 'DE': ['GERMANY'], 'NL': ['NETHERLAND', 'NETHERLANDS']}
```

---

## Pending items / open loops

1. **Webform HTML embed snippet** — endpoint live, need a copy-pasteable `<form>` snippet for external sites
2. **Re-classify on classifier_version bump** — already works automatically, but consider showing pending-classification count on /leads page
3. **Sector backfill on customers** — historical CSV provided sectors directly; KVK rows have no sector yet. Could run classifier across KVK too (would need a similar column on `kvk_companies`)
4. **Lead-to-customer match propagation** — when a lead is marked `existing_customer`, copy the customer's `main_sector` if the classifier said `Uncategorized`
5. **Trengo widget GTM tracking** — separate from app, user has setup guide already

## Common debug commands

```bash
# Live status snapshot
curl -s -u "schild:Schildinc#01" https://schild-prospect-engine-production.up.railway.app/api/kvk/progress | python3 -m json.tool

# Quick sector counts on facebook_leads
curl -s -u "schild:Schildinc#01" https://schild-prospect-engine-production.up.railway.app/leads | grep -oE "[A-Z][a-z &]+: <strong>[0-9,]+" | head -12

# Railway logs (last 200 lines)
railway logs --service schild-prospect-engine | tail -200

# Find a route handler quickly
grep -n '@app.get\|@app.post' app/main.py | grep -i "<name>"

# Test the webform endpoint
curl -X POST -H "Content-Type: application/json" \
  -d '{"email":"test@bikecity.nl","company_name":"Test Bike Shop"}' \
  -u "schild:Schildinc#01" \
  https://schild-prospect-engine-production.up.railway.app/api/leads/webform
```

---

## Style / language

- **UI is English** (translated from Dutch). Don't reintroduce Dutch labels.
- **No emoji in committed Python/SQL** unless user explicitly asks. Templates DO use emoji freely.
- **Co-Author tag**: every commit ends with `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>` (or whichever model is active).
- **Sector names**: exact case-sensitive matches with `SECTORS` constant in `app/lead_classifier.py`. Don't lowercase or rename.

---

## Recent session highlights (2026-05-22 → 2026-06-02)

- **Strict KVK matching** — removed 1,932 false klants (was 2,059 → now 127 true klants)
- **Search-attempts tracking** — KVK rows now show 🆕/1×/2×/🏪 badges, agent prioritizes never-searched first
- **Offline-only label** — businesses with zero web presence (currently 6 records)
- **Full English UI** — entire `/kvk` page + nav translated from Dutch
- **Hands-free agent** — no more prompts, CAPTCHAs auto-skip
- **One-click verify/reject email** — ✓/✗ buttons in KVK rows
- **always-on agent installer** — launchd plist auto-starts on login
- **Lead sector classifier** — 11-sector keyword matcher, multi-language, 10k rows/sec
- **Classifier daemon** — runs every 60s on the FB leads pool
- **Webform endpoint** — `POST /api/leads/webform`, CORS-open, classifies inline
- **/leads sector chips + filter** — clickable counts per sector
- **Migration 0012** — main_sector + classifier_version on facebook_leads
- **Customer+Lead combined exports** — combined CSVs (Bike FR+DE, SteelWork NL+DE) with `source` column

## How to start a new chat with this context

1. Open Claude Code (`claude` command) in this directory:
   ```bash
   cd "/Users/kevinolla/AI Project/B2B Prospect tool"
   claude
   ```
2. This `CLAUDE.md` is auto-loaded — Claude sees the full project state immediately.
3. Tell Claude what you want next; no need to re-explain history.
