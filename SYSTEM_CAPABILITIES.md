# Schild Inc — B2B Prospect Engine: Capabilities Overview

> A handoff document describing what this system **does today** (state as of 2026-06-26),
> written so another AI or engineer can understand the tool without reading the codebase.

---

## 1. What it is

A self-hosted **B2B cold-outreach + lightweight CRM** for **Schild Inc** (a Dutch
metal-label manufacturer). It finds prospective business customers (primarily
bicycle shops, plus ~10 other sectors across NL/DE/FR/BE/UK/US), enriches them with
contact details, deduplicates against existing customers, and runs **tracked cold
email campaigns** — then manages the replies in a Trengo-style shared inbox.

- **Production URL:** https://schild-prospect-engine-production.up.railway.app
- **Auth:** HTTP Basic on every page (`schild` / `Schildinc#01`), plus an app-level
  agent login layer with roles (owner = admin, teammates = limited).
- **Hosting:** Railway (FastAPI container + managed Postgres). Deploy is manual
  (`railway up`) — pushing to GitHub does **not** auto-deploy.

## 2. Tech stack

FastAPI + SQLAlchemy 2.0 + Alembic + psycopg3 + Postgres. Server-rendered Jinja2
templates with vanilla JS. Background work runs as in-process threaded daemons
started on app startup. Discovery/scraping uses Playwright + Trafilatura + RapidFuzz.

## 3. Core data (live row counts, approximate)

| Table | Rows | Purpose |
|---|---|---|
| `kvk_companies` | ~3,990 | Dutch Chamber of Commerce companies — the main cold-outreach pool |
| `customers` | ~3,256 | Existing paying customers (Stripe + historical CSV) — used as the suppression/exclusion set |
| `facebook_leads` | ~8,500 | Inbound leads (Facebook Lead Ads sheet + webform submissions) |
| `prospects` | ~68 | Earlier Google Places imports |
| `contacts` / `conversations` / `messages` | — | CRM contact hub + shared-inbox data |
| `email_campaigns` / `email_campaign_recipients` / `email_events` | — | Campaign engine + open/click/unsubscribe tracking |

**Sector taxonomy (11 fixed sectors):** Bike, Candles, Woodwork, Furniture, SteelWork,
Music, Fashion, Liquor & Bottles, Service, Art, Uncategorized. A fast regex keyword
classifier (NL/DE/FR/EN, ~10k rows/sec) tags leads automatically.

## 4. What it can do — capabilities

### A. Prospect discovery (find a company's website + contact email)
- **Open / non-Google engine (default, `DISCOVERY_ENGINE=open`):** for each company
  it queries a self-hosted **SearXNG** meta-search, crawls the candidate site with
  Playwright/Trafilatura, and extracts a public email + phone. A **precision-first
  scorer** only auto-accepts a website when a *distinctive* token of the company name
  appears in the domain; directory/aggregator/marketplace domains (telefoonboek,
  cylex, marktplaats, etc.) are blocklisted. Anything uncertain is routed to a human
  **review queue** rather than guessed.
- **Google fallback (optional):** Google Places / Playwright-Google scraping still
  exists and can be re-enabled via config, but is no longer the default.
- **Operational note:** open discovery is live but **yield is currently low** — many
  tiny shops have no website, and SearXNG result quality on obscure names is weak, so
  most rows correctly land in "needs review." It is brand-safe (no junk auto-accepted),
  not high-throughput.

### B. Customer suppression / matching (don't email existing customers)
- **Strict matching:** a `kvk_companies` row is flagged as an existing client only on
  (1) exact email match, or (2) exact normalized company-name + country match. Fuzzy
  and domain matching are deliberately disabled to avoid false suppression.
- The campaign builder also re-checks suppression at send time and honors the
  unsubscribe list.

### C. Lead intake
- **Webform endpoint** `POST /api/leads/webform` (CORS-open, JSON or form) — accepts
  inbound leads from external sites, classifies the sector inline, dedupes, and
  re-checks against customers + KVK.
- **Google Sheet auto-sync** — Facebook Lead Ads sheet is polled every 15 min and
  upserted.

### D. Email cold-campaign engine (the primary output)
- **Audiences:** KVK companies (excludes existing clients + no-email), Facebook/web
  leads, or existing customers — filterable by tier/sector/country, or by explicit IDs
  from a list page.
- **Templates:** 5 seeded starter templates (cold intro/follow-up, warm intro/follow-up,
  VIP) plus operator-editable custom templates. Merge fields: `{{company_name}}`,
  `{{contact_name}}`, `{{city}}`, `{{country}}`, `{{website}}`, `{{sender_name}}`,
  `{{reply_to}}`, `{{unsubscribe_url}}`.
- **Sending transport (provider-abstracted):** `MAIL_PROVIDER` selects the transport —
  **Resend** (currently live), Brevo, generic SMTP, Gmail SMTP, Gmail API, or console.
  Reply-to is **always forced** to `sales@schildinc.com`.
- **Tracking & compliance:** open pixel, click-redirect, and RFC 8058 one-click
  unsubscribe (public endpoints, no auth). Unsubscribes add a suppression entry.
- **Throttling:** a background daemon drains campaigns one at a time, spaced (~8s) with
  a daily cap, for sender warm-up. Campaigns can be scheduled, paused, and test-sent.

### E. CRM — contacts + shared inbox (Trengo-style)
- **Contact Hub** (`/contacts`): identity resolution + backfill from customers, KVK,
  leads, and prospects into a unified contact with a timeline.
- **Shared Inbox** (`/inbox`): conversations/messages/assignment/statuses, canned
  replies, labels/teams, @mentions + notifications, attachments. 3-pane UI.
- **Two-way email:** can poll a connected Gmail for replies and thread them (currently
  inactive — no Gmail connected; replies flow to Trengo instead).
- **WhatsApp + Instagram (official Meta APIs):** inbound threading into the inbox +
  replies within the 24h service window. Cold DMs are intentionally **not** automated
  (ToS/ban risk). LinkedIn is a manual helper only. These require Meta env vars to be
  configured to activate.

### F. Roles, reporting, audit
- Agent login with PBKDF2 + signed session cookies; owner = admin, teammates limited.
- `/reports` dashboards (email/inbox/contact rollups, per-agent, avg first-response),
  a live SSE unread badge, and an admin-only `/audit` log of sensitive actions.

### G. Exports
- CSV exports for customers and KVK with rich filters; combined customer+lead exports
  with country-name mapping.

## 5. Current operational status (important for an AI taking over)

| Capability | Status |
|---|---|
| Cold email sending | ✅ **Live via Resend** — first 16-shop batch sent successfully; domain DKIM verified, lands in Primary inbox |
| `send`/test DNS hardening | ⚠️ `send.schildinc.com` MX + SPF TXT not yet resolving (DKIM alone is delivering fine) |
| Open discovery (SearXNG) | ✅ Deployed & brand-safe, ⚠️ low yield on micro-shops |
| Gmail API send / inbound | ⛔ Not connected (no `gmail_accounts` row) — provider path used instead |
| WhatsApp / Instagram | ⚙️ Built, needs Meta env vars to activate; IG currently kept in Trengo |
| Customer suppression | ✅ Strict matching active |
| Data quality caveat | ⚠️ Some KVK enriched emails are mis-matched; recipient lists must be vetted before sending |

## 6. How to interact (key endpoints)

- Pages: `/kvk`, `/customers`, `/leads`, `/contacts`, `/inbox`, `/emails`, `/reports`,
  `/review` (discovery/match/tier review queues), `/audit`, `/login`.
- Campaigns: `POST /emails/campaigns/create`, `POST /emails/campaigns/{id}/send`,
  `/test`, `/pause`, `/schedule`. Tracking: `/e/o/{token}.gif`, `/e/c/{token}`,
  `/e/u/{token}`.
- Lead intake: `POST /api/leads/webform`. Discovery batch: `POST /review/discovery/run-batch`.

## 7. Known limitations / honest caveats

- Not "bug-free" — discovery yield is modest, and KVK contact data has noise that
  requires human/automated vetting before outreach.
- Cold messaging targets only KVK + Maps prospects and **excludes** existing customers
  and inbound-form leads, by design.
- The sender is freshly warmed; volume should ramp gradually (~15–30/day initially).
- No automated cold DMs on Instagram/LinkedIn (compliance/ban risk) — inbound + manual
  only.

---

*For deeper architecture, gotchas, and env vars, see `CLAUDE.md` in the repo root.*
