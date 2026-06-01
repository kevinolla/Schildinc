# Schild Inc B2B Prospect Engine — Project Memory

Last updated: 2026-05-22

This file is read automatically by Claude Code when starting a new session
in this repo. It captures architecture, history, gotchas, and pending work
so we don't re-explain from scratch each time.

---

## What this is

A B2B outreach engine for **Schild Inc** (Dutch metal-label manufacturer
targeting bicycle shops and similar SMBs across NL/DE/BE/FR/UK/US/etc.).

- **Production**: https://schild-prospect-engine-production.up.railway.app
- **Auth**: `schild` / `Schildinc#01` (HTTP Basic on every page)
- **GitHub**: https://github.com/kevinolla/Schildinc.git (main = trunk, autodeploy NOT configured — must `railway up`)
- **Owner email**: schild.inc.official@gmail.com
- **Stack**: FastAPI + SQLAlchemy + Alembic + Postgres on Railway, Jinja2 templates, vanilla JS

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  Railway container                      │
│  ┌────────────────────────────────────────────────┐    │
│  │  FastAPI app (app/main.py)                     │    │
│  │  - HTTP routes for /kvk, /customers, /leads,   │    │
│  │    /prospects, /queue, /suppression, /logs     │    │
│  │  - Agent API: /api/kvk/agent/{pending,result}  │    │
│  ├────────────────────────────────────────────────┤    │
│  │  Background daemons (lifespan startup)         │    │
│  │  - KVK auto-enrich scheduler (3 workers,       │    │
│  │    polls every 30s)                            │    │
│  │  - FB-leads sheet sync (every 15 min)          │    │
│  └────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
              ↑                       ↓
              │ HTTP                  │ Postgres
              │                       ↓
   ┌──────────────────┐    ┌──────────────────┐
   │ Local Playwright │    │ Railway Postgres │
   │ agent (laptop)   │    │ (3990 KVK rows,  │
   │ scripts/email_   │    │ 8508 FB leads,   │
   │ agent.py         │    │ 3256 customers)  │
   └──────────────────┘    └──────────────────┘
```

The **local agent** runs on the user's Mac because Google/Bing return
JS-only stub pages to cloud IPs (Railway). Residential IP → real
snippets in the rendered DOM.

---

## Tables (live row counts as of 2026-05-22)

| Table | Rows | Purpose |
|---|---|---|
| `customers`        | 3,256 | Paying customers (Stripe + Schild historical CSV) |
| `kvk_companies`    | 3,990 | Dutch Chamber of Commerce import — outreach pool |
| `prospects`        |    68 | Earlier Google Places imports |
| `facebook_leads`   | 8,508 | FB Lead Ads sheet + 50k-row historical CSV |
| `invoices`         |   ~7k | Linked to customers |

---

## Key modules

| Path | Role |
|---|---|
| `app/main.py` | All HTTP routes (single big file ~2,400 lines) |
| `app/models.py` | SQLAlchemy models — KvkCompany, Customer, Prospect, FacebookLead, Invoice, SuppressionEntry, etc. |
| `app/matching.py` | **STRICT** KVK↔Customer matching: exact email OR exact (name + country). Anything fuzzy was deliberately removed. |
| `app/kvk_enrichment.py` | Background scheduler that crawls KVK records (3 workers, 6s Playwright timeout). Stage-by-stage: Places → Playwright site crawl → MX-guess `info@domain` |
| `app/discovery.py` | Core Playwright scraper (used by both scheduler + agent) |
| `app/email_guesser.py` | MX-validated `info@<domain>` pattern fallback. Skips free webmail. |
| `app/brave_search.py` | Brave Search API wrapper. Circuit-breaker trips after 5 consecutive 402s (out-of-budget). |
| `app/bing_search.py` | HTML scrape — cloud IP gets useless SPA stub, kept for completeness |
| `app/playwright_search.py` | Playwright Google scraper — works from cloud, but slow (~5-8s/query); module-level `_LAUNCH_LOCK` serializes launches |
| `app/google_search.py` | Google CSE wrapper — DEPRECATED (Google killed "Search the entire web" toggle, so CSEs only search 1 placeholder domain) |
| `app/facebook_leads.py` | FB sheet auto-sync daemon + flexible importer (handles both live sheet AND historical CSV column shapes) |
| `app/customer_normalizer.py` | Schild Inc historical customer CSV importer — aggregates 3,078 order-lines to 2,052 customers |
| `app/country_codes.py` | ISO-2 ↔ name registry. `to_iso2(value)` canonicalizes anything ("Netherlands"/"NL"/"NLD"/"NET" → "NL") |
| `app/klaviyo_sync.py` | Klaviyo v3 profile push (list `XHgkXM` = "KVK Lead List") |
| `scripts/email_agent.py` | **Local browser agent** — runs on user's laptop, real Chromium, residential IP. Hands-free (no prompts). |
| `scripts/install-agent-daemon.sh` | macOS launchd installer for always-on agent |
| `scripts/com.schildinc.kvk-agent.plist` | The launchd config it installs |

---

## Migrations (current head: 0011)

| Rev | Adds |
|---|---|
| 0001 | Initial schema |
| 0002 | Prospect discovery + tiering fields |
| 0003 | Contact channels (phone, WA, IG, LI) on prospects |
| 0004 | Discovery lists |
| 0005 | KVK tables (companies, establishments, import logs) |
| 0006 | KVK fields on prospects table |
| 0007 | KVK social columns (whatsapp_number, whatsapp_url, instagram_url, linkedin_url) |
| 0008 | facebook_leads table |
| 0009 | FB lead sales annotations (quality_score, progress, pic, etc.) |
| 0010 | Customer rich fields (main_sector, sub_sector, customer_segment, contact_person, phone_primary, website) |
| 0011 | KVK `search_attempts` counter + index |

Alembic runs on container startup (`alembic upgrade head` in the start command in `railway.json`).

---

## Important architectural decisions / gotchas

### KVK matching is STRICT
After the user complained about 2,059 false "Klant" flags, we rewrote `match_kvk_company()` to ONLY match on:
1. Exact email (lowercased)
2. Exact `canonical_company_name_clean` + uppercased country

**Domain matching and fuzzy name matching are DELIBERATELY REMOVED.**
Don't reintroduce them.

### Agent endpoint pagination
`/api/kvk/agent/pending`:
- Filters out `discovered`/`partial`/`no_contacts` records
- Filters out `already_client_flag = True`
- Filters `search_attempts < max_attempts` (default 2)
- Orders by `search_attempts ASC, id ASC` (never-searched first)

`/api/kvk/agent/result` ALWAYS increments `search_attempts += 1`. So the priority shifts naturally.

### Local agent CAPTCHA handling
No prompts ever. When Google challenges → log + `wait_for_timeout(90s)` + skip + move on. Record stays in `/agent/pending` for a later retry.

### KVK enrichment scheduler stability
3 workers, 6s Playwright timeout, periodic stuck-record cleanup every batch. Sched runs in lifespan startup via `start_auto_enrichment_scheduler()` in `app/kvk_enrichment.py`. Module-level `_scheduler_started` flag makes it idempotent.

### Playwright threading
`sync_playwright()` from multiple threads = `RuntimeError: Racing with another loop`. Fixed by a module-level `threading.Lock()` in `app/playwright_search.py` that serializes only the launch step.

### Brave Search circuit breaker
After 5 consecutive `402 Payment Required` responses, `is_enabled()` returns `False` for the rest of the UTC day. State in `_breaker_state` dict. `_record_success()` resets the counter.

### Country code canonicalization
The user's historical CSVs have mixed country values: `Netherlands` / `NL` / `NLD` / `NET` (from the old normalizer's 3-letter truncation). Use `app.country_codes.to_iso2()` for any new normalization. Live DB was backfilled — 1,017 rows changed.

### Klaviyo
- Private key in `KLAVIYO_PRIVATE_API_KEY` env var (Railway)
- Target list: `XHgkXM` ("KVK Lead List")
- v3 API, revision `2024-02-15`
- Push: `POST /kvk/push-klaviyo`

### Google Sheet auto-sync (FB leads)
- Sheet: `10k2UB3qefKvskF1YemikhVCPk0JI8xmScH2dj_I7h5g`, gid `1219149797`
- Public CSV export URL (no OAuth needed)
- Polled every 15 min by `_fb_sync_loop()`
- Importer dedupes by `fb_lead_id` via Postgres `ON CONFLICT (fb_lead_id) DO UPDATE`

---

## Environment variables (Railway)

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection (auto-injected) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Basic auth credentials |
| `GOOGLE_PLACES_API_KEY` | Places API (key starts `AIzaSyDUWj…`) |
| `BRAVE_API_KEY` | Brave Search (out of credit currently) |
| `BRAVE_DAILY_LIMIT` | Default 300 |
| `KLAVIYO_PRIVATE_API_KEY` | `pk_6341a9b1914615abc527080b0ef797f2aa` |
| `KLAVIYO_LIST_ID` | `XHgkXM` |
| `KVK_AUTO_ENRICH_ENABLED` | `true` |
| `KVK_AUTO_ENRICH_BATCH` | Default 12 |
| `KVK_AUTO_ENRICH_INTERVAL` | Default 30 (seconds between batches) |
| `KVK_AUTO_ENRICH_WORKERS` | Default 3 |
| `PLAYWRIGHT_TIMEOUT_MS` | Default 6000 |
| `FB_LEADS_AUTO_SYNC_ENABLED` | `true` |
| `FB_LEADS_AUTO_SYNC_INTERVAL` | Default 900 |

---

## How to develop / deploy

### Local dev
```bash
cd "/Users/kevinolla/AI Project/B2B Prospect tool"
source .venv/bin/activate
# Run server pointing at LOCAL sqlite (or set DATABASE_URL for prod)
uvicorn app.main:app --reload --port 8000
```

### Deploy to Railway
```bash
cd "/Users/kevinolla/AI Project/B2B Prospect tool"
railway login        # if token expired (every few weeks)
railway up --service schild-prospect-engine
```
Build takes ~10 min (nixpacks installs Playwright + Chromium every time).

### Run the local agent (manual)
```bash
cd "/Users/kevinolla/AI Project/B2B Prospect tool"
source .venv/bin/activate
python scripts/email_agent.py            # default (visible Chrome, 1.5s delay)
python scripts/email_agent.py --headless --delay 3.0 --quiet --max 100
```

### Install always-on agent (launchd)
```bash
bash scripts/install-agent-daemon.sh
```
Starts immediately + on every login. Logs: `~/Library/Logs/schild-kvk-agent.log`. Unload: `launchctl unload ~/Library/LaunchAgents/com.schildinc.kvk-agent.plist`.

---

## Production DB access (read-only debug)

```python
from sqlalchemy import create_engine, text
e = create_engine('postgresql+psycopg://postgres:LrTsgCYOvlJPvbcWgpqWUGycnyYUjYLq@switchyard.proxy.rlwy.net:13263/railway')
with e.connect() as c:
    print(c.execute(text("SELECT enrichment_status, COUNT(*) FROM kvk_companies GROUP BY 1 ORDER BY 2 DESC")).fetchall())
```

**Don't commit this connection string.** It rotates if Railway regenerates DB creds.

---

## Pending items / open loops

1. **Latest commits not yet deployed**: `143592b` (search_attempts), `1dbd812` (offline-only), `687bee1` (English UI + verify/reject buttons), `9b98a59` (multi-country checklist + name display) — user needs to run `railway login && railway up --service schild-prospect-engine`. Railway CLI OAuth token expired in last session.
2. **Always-on agent**: code is ready (`scripts/install-agent-daemon.sh`), user hasn't installed yet
3. **Customer DB import**: 2,052 of 2,092 unique customer names ingested (40 collapsed via entity_id slug collisions — known, expected behavior)
4. **FB leads classification**: 8,345 of 8,508 still marked `new` — should re-run `_classify_lead()` across the table after a customer DB refresh

## Common debug commands

```bash
# Live status snapshot
curl -s -u "schild:Schildinc#01" https://schild-prospect-engine-production.up.railway.app/api/kvk/progress | python3 -m json.tool

# Railway logs (last 200 lines)
railway logs --service schild-prospect-engine | tail -200

# Find a route handler quickly
grep -n '@app.get\|@app.post' app/main.py | grep -i "<route_name>"

# Validate all migrations apply cleanly
alembic upgrade head
```

---

## Style / language

- **All UI is English** (translated from Dutch on 2026-05-22). Don't reintroduce Dutch labels.
- **No emoji in committed code** (Python source, SQL) unless user explicitly asks. UI templates DO use emoji freely.
- **Co-Author tag**: every commit ends with `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>` (or whatever model is active).
