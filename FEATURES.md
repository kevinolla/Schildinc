# Schild Inc — B2B Prospect Engine: Full Capabilities

> Authoritative "what this system does today" reference for humans and AI agents.
> Last updated: 2026-07-13. For architecture/gotchas see [CLAUDE.md](CLAUDE.md);
> for the (partly implemented) simplification plan see [REDESIGN_SPEC.md](REDESIGN_SPEC.md).

---

## 1. What it is

A self-hosted **B2B cold-outreach engine + lightweight CRM + paid-media data pipeline** for
**Schild Inc** (Dutch maker of custom metal labels, aluminium plates, hard badges, bike labels).
It **finds** prospective business customers, **enriches** them with contact data, **excludes**
existing customers, runs **tracked, personalized cold-email campaigns** from multiple brand
domains, and **exports privacy-aware audiences** for Meta and Google Ads.

- **Production:** https://schild-prospect-engine-production.up.railway.app
- **Auth:** HTTP Basic on every page (`schild` / `Schildinc#01`) + app-level agent login/roles.
- **Stack:** FastAPI + SQLAlchemy 2 + Alembic + Postgres on Railway. Jinja2 + vanilla JS. In-process
  threaded background daemons. Discovery via OpenStreetMap Overpass + SearXNG + Playwright/Trafilatura.
- **Deploy:** manual `railway up` (GitHub push does NOT auto-deploy). Alembic runs on container start.

---

## 2. Navigation (simplified to 5)

`app/templates/base.html` — flat sidebar: **📊 Overview · 🗄️ Cold Database · ✉️ Campaigns ·
👥 Customers · 📥 Leads**, plus a **⚙️ Settings & more** menu (Reports, Setup, Suppression,
Templates, Audit, Logs). Legacy pages (`/inbox`, `/review/*`, `/sequences`, `/queue`, `/kvk`,
`/prospects`) still work by URL but are off the main nav.

---

## 3. Core features

### A. Directory Crawler (`/crawler`) — free, always-on lead discovery
`app/crawler.py`. Operator creates **CrawlJob** rows (sector(s) × country, optional cities).
Up to `CRAWLER_MAX_CONCURRENT_JOBS` (prod=6) run at once; resumable, per-job cap (adjustable inline,
ceiling 50k). **100% free — no Google Cloud:**
- **OpenStreetMap Overpass** (primary): structured business listings by sector tag
  (`shop=bicycle`, `craft=carpenter`, …), often with a tagged public email.
- **SearXNG** (fallback): localized "sector-term city" web search, directory-host blocklist.
Harvested businesses dedupe against `prospects` + KVK, store into `prospects` (source='crawler')
with sector/country/job provenance, then a public email is extracted (visible-page scan +
MX-validated `info@domain`). First prod harvest: ~13k businesses, ~8k emails (Bike DE/FR,
Woodwork/Furniture NL/DE, Steel NL/DE). *(Google Places is disabled — the Cloud project is suspended;
the crawler was built to not need it.)*

### B. Cold Database (`/database`) — unified, sector-downloadable
`app/templates/database.html`. One page over the sector-complete crawler/Maps `prospects`:
sector chips (click=filter, ⬇=download that sector), 5 filters (sector/country/source/has-email/
search), paginated table, and a **Download Center** (per-sector counts + CSV). Sector CSV export:
`GET /database/export.csv?sector=&country=&source=&has_email=` (self-describing filenames).
**"Email this sector"** one-click → `/emails/campaigns/new?audience=prospect&sector=&country=`.

### C. KVK list (`/kvk`)
~3,990 Dutch Chamber-of-Commerce businesses (originally bike-store registries; **now
sector-classified** — migration 0029). Strict matching flags existing customers. Import,
enrich, export, push-to-Klaviyo.

### D. Customers (`/customers`) — exclusion set
~3,256 paying customers (Stripe + historical CSV), aggregated with `lifetime_amount_paid`,
`invoice_count`, first/last paid dates, sector, phone, website. Used as the suppression/exclusion
set and the source for lookalike/Customer-Match audiences. Filterable CSV export.

### E. Leads (`/leads`) — inbound
~8,745 Facebook Lead Ads leads (auto-synced from a Google Sheet) + webform submissions. Sector
auto-classified (keyword classifier, NL/DE/FR/EN). Carries `email_marketing_consent`, `fb_lead_id`,
campaign name, phone.

### F. Email campaigns (`/emails`) — the primary output
`app/email_engine.py`, `email_providers.py`, `email_library.py`, `sending_domains.py`.
- **Audiences:** KVK / crawled prospects / leads / customers, filterable by sector/tier/country, or
  explicit IDs. Existing customers + suppressed excluded; suppression re-checked at send time.
- **Multi-domain sending** (`app/sending_domains.py`): pick a brand identity per campaign —
  **Schild Inc** (sales@schildinc.com), **Schild Labels** (sales@schildlabels.com), **Schild Inc NL**
  (verkoop@schildinc.nl). Reply-To is allowlist-guarded to Schild domains (can't leak). `SEND_VERIFIED_DOMAINS`
  gates which are allowed to send. Website redirects: schildlabels.com / schildinc.nl → schildinc.com.
- **Providers (abstracted):** Resend (live), Brevo, SMTP, Gmail SMTP, Gmail API, console. RFC 8058
  one-click List-Unsubscribe on Resend.
- **Templates:** seed v3 — larger readable type (Montserrat/Arial 14px), one CTA button, localized
  cold intro + follow-up in **EN/NL/DE** + VIP. `{{opener}}` per-recipient first line, city+language
  aware, empty-safe. Merge fields + tracking (open pixel, click redirect, unsubscribe).
- **Visual template editor** (`/emails/templates/builder`, `app/static/template_builder.js`):
  dependency-free **drag-and-drop block editor** (Heading, Text, Button, Image, Divider, Spacer,
  **Signature**). Live inspector, merge-tag inserter, live preview, compiles to email-safe table HTML.
  Persists a `builder_json` block model so templates re-open visually. The **Ruben signature** is a
  hosted banner (`/static/email/signature-ruben.png`) with an editable HTML fallback.
- **Sending:** background daemon drains `sending`/scheduled campaigns at a daily cap
  (`GMAIL_DAILY_LIMIT`, ~80) with per-send spacing; dry-run, test-send, pause/schedule.

### G. Paid-media audience exports (offline tooling — `scripts/`)
Read-only pipelines that build **privacy-aware** Meta + Google Ads audiences. **Output is written
OUTSIDE the git repo** (PII) and is never auto-uploaded to any platform.
- `build_audiences.py` — CRM → normalized/deduped records → funnel stage (customer / high-intent /
  cold / suppressed) → recency+value+intent scores → Meta Custom-Audience/Lookalike + Google
  Customer-Match + exclusion files, quality reports, field mapping, dedup + suppression audits.
  **Only customers + own inbound leads are ad-eligible; cold scraped prospects are excluded** (no
  lawful basis / platform-policy risk).
- `clean_trengo.py` — cleans a Trengo ticket export: aggregates by email, removes deal/order/reorder
  + already-purchased, keeps no-deal/no-response/mockup/quote, splits into
  **Quote-not-purchased** vs **No-deal/No-response** retargeting audiences (+ NL/DE geo splits).
- `build_cold_dataset.py` — normalizes KVK + crawled emails into Meta/Google column format, split by
  country / sector / country×sector. Labelled for **cold-email + exclusion use only** (not targeting).
- `backfill_kvk_sector.py` — one-time idempotent classify of KVK sectors (migration 0029).
All read `AUDIENCE_DB_URL` from the env (no committed secret).

### H. CRM support (present, lower emphasis)
Contact hub (`/contacts`, identity resolution), shared inbox (`/inbox`, Trengo-style — Trengo owns
replies in practice), WhatsApp/Instagram webhooks (built, need Meta env vars), reports (`/reports`),
audit log (`/audit`), roles/login (`app/auth.py`).

---

## 4. Data model (Postgres, Alembic head 0029)

| Table | Rows | Purpose |
|---|---|---|
| customers / invoices | 3,256 / 1,780 | Paying customers (aggregated value+recency) + orders |
| kvk_companies | 3,990 | Dutch cold pool — now sector-classified |
| facebook_leads | 8,745 | Inbound Meta Lead Ads leads |
| prospects | 13,426 | Crawler + Maps cold businesses (sector-tagged) |
| crawl_jobs | — | Crawler job control + live counters (0026/0027) |
| email_templates / campaigns / recipients / events | — | Campaign engine + tracking (0013, builder_json 0028) |
| suppression_entries | 2 | Unsubscribes |
| gmail_accounts, contacts, conversations, … | — | CRM/email infra |

**Migrations added this era:** 0026 crawl_jobs, 0027 crawl-job cities, 0028 template builder_json,
0029 KVK sector.

**Sector vocabulary (11 canonical):** Bike, Candles, Woodwork, Furniture, SteelWork, Music, Fashion,
Liquor & Bottles, Service, Art, Uncategorized — `app/lead_classifier.py` (keyword, NL/DE/FR/EN,
~10k rows/sec). Export vocab maps these to bike_shop / woodworker / furniture_maker /
product_manufacturer / etc.

---

## 5. Known data gaps (fix to unlock more)

1. **No web-event tracking** (Meta Pixel/CAPI, GA4/GTM): no form-start/checkout/pricing-visit events,
   no GCLID/GBRAID/WBRAID/FBCLID → blocks abandoner/checkout retargeting + conversion measurement.
   **Highest-impact fix.**
2. **No quote/deal object** in the CRM → no formal open-quote nurture audience (Trengo labels are the
   current proxy).
3. **Sparse phone/postal on customers** (partly recovered from `full_address` + `contacts`).
4. **No product-interest field**; language is inferred from country.
5. **Customer email duplication** (~1,014 dup emails) — dedupe entities at source.
6. **Google Places / Cloud project suspended** — crawler avoids it; KVK enrichment still calls it.

---

## 6. Operating notes

- **Deploy:** `railway up --service schild-prospect-engine` (~10 min; nixpacks reinstalls Chromium).
  Migrations auto-run on start. GitHub push alone does nothing.
- **Compliance stance baked in:** cold scraped contacts are never uploaded as ad Custom Audiences
  (policy + GDPR); only customers and the company's own engaged leads are ad-eligible; opt-outs +
  employees suppressed; audience PII kept outside git.
- **Secrets:** rotate the Postgres password (it was in git history); all tooling reads env vars now.

---

## 7. How to improve it (for the next builder)

- Ship the rest of [REDESIGN_SPEC.md](REDESIGN_SPEC.md): auto-send drip UI, right-drawer detail views.
- **Install Meta Pixel + CAPI and GA4/GTM** on the site + quote/sample forms → unlocks the biggest
  set of retargeting/measurement capabilities and the missing audiences.
- Add a **quotes/deals object** → real pipeline stages + open-quote audiences.
- Make audience exports a **UI feature** (button on `/customers` and `/database`) instead of scripts.
- **Reconnect a working Google Places key** (new Cloud project) for richer KVK enrichment, or drop it.
- Auto-classify sector **on import** for KVK + new prospects (backfill logic already exists).
- Verify **schildlabels.com + schildinc.nl** in Resend (`DOMAIN_SETUP.md`) to send from all brands.
