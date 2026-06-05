# Schild Inc — In-House CRM Build Specification ("The Command")

> **Purpose**: upgrade the current *Schild Inc CRM MVP / B2B Prospect Engine*
> into a full in-house CRM: one place to **collect every audience contact**,
> **reach them from our official channels** (email, WhatsApp, phone), and run a
> **Trengo-style shared support inbox** with a built-in **VoIP softphone** that
> can **dial, record, and transcribe** calls.
>
> This document is the master build plan. It is split into:
> - **Part A** — feature parity: everything the current CRM MVP already does (baseline we must keep).
> - **Part B** — the enhancement modules you requested.
> - **Part C** — architecture, data model, providers, costs, phased roadmap, decisions needed.
>
> Status legend:  ✅ done · 🟡 partial · 🔲 to build

---

## Part A — Current CRM MVP feature inventory (parity baseline)

Stack: FastAPI + SQLAlchemy + Alembic + Postgres on Railway, Jinja2 server-rendered pages, vanilla JS. HTTP Basic auth on every page.

### A1. Audience / data collection (already strong)
- ✅ **Customers** (`/customers`) — 3,256 paying customers (Stripe + historical CSV), rich fields (sector, segment, contact, phone, website, lifetime value), analytics page, CSV export with filters.
- ✅ **KVK Companies** (`/kvk`) — 3,990 Dutch Chamber-of-Commerce outreach pool; per-record detail, enrichment, tiering, email verify/reject, matching, CSV export, Klaviyo push.
- ✅ **Leads** (`/leads`) — Facebook Lead Ads + webform leads (~8.5k), sector classifier, filters, CSV import/sync.
- ✅ **Prospects** (`/prospects`) — Google Places imports, discovery, review workflow.
- ✅ **Webform ingest** (`POST /api/leads/webform`) — CORS-open, classifies inline, dedupes.
- ✅ **Importers** — customers, invoices, KVK companies/establishments, prospects, rich customer CSV.
- ✅ **Enrichment** — KVK auto-enrich daemon (Playwright), local browser agent (residential IP), email guesser (MX), Brave/Bing/Google search.
- ✅ **Matching** — STRICT KVK↔Customer (exact email OR exact name+country). Lead↔Customer/KVK match status.
- ✅ **Sector classifier** — 11-sector regex matcher, daemon every 60s.

### A2. Outreach (current)
- ✅ **Email Campaigns** (`/emails`) — NEW Gmail-backed engine: 5 starter templates, audience builder (KVK/leads/customers), open/click tracking, GDPR unsubscribe, throttled gradual sender daemon. *(Built in the previous session.)*
- 🟡 **Queue** (`/queue`) — older prospect-centric outreach (Resend/SMTP, Dutch templates, daily queue build/send, send window, cooldown). Will be folded into the new engine over time.
- ✅ **Suppression** (`/suppression`) — opt-out / do-not-contact list, honored at send time.

### A3. Integrations & ops
- ✅ **Stripe** webhook sync (`/webhooks/stripe`), invoices.
- ✅ **Klaviyo** v3 profile push.
- ✅ **Google Sheet** auto-sync for FB leads (every 15 min).
- ✅ **Agent API** (`/api/kvk/agent/*`) for the local Playwright enrichment agent.
- ✅ **Logs** (`/logs`), **Dashboard** (`/`), **health check**, CSV exports everywhere.
- ✅ Background daemons: KVK enrich (3 workers), FB sync, lead classifier, email sender.

> **Parity rule**: none of the above may regress. The new CRM is an *additive*
> upgrade. The current nav (Dashboard, Customers, Leads, KVK, Prospects, Email
> Campaigns, Queue, Suppression, Logs) stays; we add **Contacts**, **Inbox**,
> and **Calls**.

---

## Part B — Enhancement modules (your requests)

### B1. 🔲 Unified Contact Hub ("360° audience record")
**Goal**: one canonical contact record per person/company, merging Customers +
KVK + Leads + Prospects, so support always sees the full picture.

- New `contacts` table = the master identity. Each contact links to its source
  rows (customer_id, kvk_company_id, facebook_lead_id, prospect_id).
- Identity resolution: merge on exact email / phone (E.164) / name+country —
  reuse the existing strict matching philosophy (no fuzzy false-merges).
- **Contact profile page** (`/contacts/{id}`): all emails, phones, WhatsApp,
  socials, sector, tier, lifetime value, **and a unified activity timeline**
  (emails sent/opened/clicked, WA messages, calls, notes, status changes).
- Channels sub-table (`contact_channels`): type (email/phone/whatsapp/instagram/
  linkedin), value, verified flag, primary flag, source.
- List view (`/contacts`) with search + filters (sector, country, tier, source,
  has-email, has-phone, last-contacted), saved segments, CSV export.
- Backfill job: build contacts from existing customers/KVK/leads/prospects.

### B2. 🔲 Unified Support Inbox (Trengo-style)
**Goal**: a shared team inbox that feels like Trengo — every channel in one
threaded view, assignable, with statuses and internal notes.

- **Conversations** model: one thread per contact+channel(s). Fields: contact_id,
  channel, subject, status (`open`/`pending`/`snoozed`/`closed`), assignee,
  last_message_at, unread flag, labels/tags.
- **Messages** model: direction (inbound/outbound), channel, body (text+html),
  attachments, sender, external_id, delivery/read status, timestamps.
- **Inbox UI** (`/inbox`): left = conversation list (filter by channel, status,
  assignee, "mine"/"unassigned"); center = thread; right = contact context panel
  (profile + timeline). Reply box switches channel (email / WhatsApp).
- Trengo-familiar features: **assign to teammate**, **internal notes** (not sent
  to customer), **canned replies / quick responses**, **labels**, **snooze**,
  **resolve/close**, **@mentions**, **typing/echo of sent**, unread counts.
- Real-time: start with short-poll (every ~5–10s) + manual refresh; upgrade to
  WebSocket/SSE in a later phase for live updates.
- **Teams & agents**: `agents` table (name, email, role: admin/agent), login,
  per-agent assignment + "round-robin"/manual routing.

### B3. 🔲 Two-way Email (extend the existing engine)
Currently email is **outbound-only** (campaigns). Upgrade to conversational:
- **Receive replies**: poll Gmail API (`users.messages.list` + history API) for
  the connected account, thread inbound replies into the Inbox by `In-Reply-To`/
  `References` + contact email. (Push via Gmail `watch`+Pub/Sub is a later optimization.)
- **Send 1:1** from a conversation (not just bulk campaigns), using the same
  Gmail send-as alias, threaded (`threadId`).
- Link campaign sends into the contact timeline.

### B4. 🔲 WhatsApp messaging (official Business API)
**Goal**: send + receive WhatsApp from our official business number, inside the Inbox.
- Use the **official WhatsApp Business Cloud API** (Meta) — the same foundation
  Trengo uses. Requires a Meta Business account, a dedicated WA number, and
  Meta-approved **message templates** for proactive (outside-24h) messages.
- Outbound free-text only allowed inside the 24-hour customer-service window;
  outside it, an approved template is required (Meta rule, not ours).
- Webhook (`/webhooks/whatsapp`) receives inbound messages + delivery/read
  receipts → threaded into Inbox.
- Outbound send via Graph API `messages` endpoint; store external message id.
- Media (images/PDF) support for both directions.
- *(Cost + approval reality in Part C.)*

### B5. 🔲 VoIP / Calling with recording + transcription
**Goal**: support dials customers **from the CRM**, using our **official phone
number**; calls are **recorded** and **transcribed**, attached to the contact.
- **In-browser softphone (WebRTC)**: a click-to-dial widget on every contact
  and conversation. Agent talks through the laptop — no desk phone needed.
- **Provider**: Twilio Programmable Voice (or Telnyx/Vonage) for the carrier
  layer + WebRTC Voice SDK. Our official number is either **ported in** or
  connected via **SIP trunk / verified caller ID**.
- **Inbound calls** ring the on-duty agent(s) in-browser; missed calls log to Inbox.
- **Recording**: provider-side dual-channel recording → stored URL on the call record.
- **Transcription**: feed the recording to OpenAI **Whisper** (or Deepgram) →
  store transcript + (optional) AI summary on the call + contact timeline.
- **Calls model**: contact_id, agent_id, direction, from/to, status, duration,
  recording_url, transcript, summary, started/ended timestamps.
- **Calls page** (`/calls`): history, filters, playback, transcript view, link to contact.
- Webhooks (`/webhooks/voice/*`) for call status + recording-ready callbacks.

### B6. 🔲 Cross-cutting
- **Activity timeline** unifying email/WA/call/note/status across modules.
- **Notifications**: in-app unread badges; optional email/desktop ping on new inbound.
- **Roles & permissions**: admin vs agent; restrict destructive ops.
- **Audit log**: who sent/called/changed what.
- **Reporting**: per-agent volume, response time, open/click/answer rates.

---

## Part C — Architecture, data model, providers, roadmap

### C1. New data model (Alembic migrations 0014+)
```
contacts                 master identity (links to customer/kvk/lead/prospect)
contact_channels         email/phone/whatsapp/social values per contact
agents                   team members (name, email, role, active)
conversations            inbox threads (contact, channel, status, assignee, labels)
messages                 inbound/outbound messages within a conversation
canned_replies           saved quick responses
calls                    VoIP call records (recording_url, transcript, summary)
call_events              raw provider callbacks
whatsapp_accounts        WA Business number + token config
voice_accounts           VoIP provider config (number, SIP, API keys)
activities               unified timeline events (denormalized for fast render)
notifications            per-agent in-app notifications
audit_logs               who did what
```
Reuse existing `gmail_accounts`, `email_*`, `suppression_entries`, `customers`,
`kvk_companies`, `facebook_leads`, `prospects`.

### C2. New modules (mirrors current file-per-concern style)
```
app/contacts.py          identity resolution + backfill + profile assembly
app/inbox.py             conversation/message logic, assignment, canned replies
app/whatsapp.py          WhatsApp Cloud API client + webhook handling
app/voip.py              Twilio/Telnyx client, click-to-dial, webhooks
app/transcription.py     Whisper/Deepgram wrapper
app/gmail_inbound.py     poll + thread inbound email replies
app/agents.py            team + auth/roles
templates/contacts.html, contact_detail.html, inbox.html, calls.html, ...
```

### C3. Stack decisions
- **Keep FastAPI + Jinja** for parity and speed of delivery. Add a small amount
  of interactivity (HTMX or vanilla fetch polling) for the inbox; add a JS
  WebRTC widget for the softphone. **No full SPA rewrite** — incremental.
- **Real-time**: phase 1 polling; phase 2 WebSocket/SSE for inbox + incoming calls.
- **Background work**: existing daemon pattern (threads) for inbound email poll,
  transcription jobs, WA/voice webhook processing.

### C4. ⚠️ Third-party providers & cost reality (important — be clear-eyed)
The new outbound channels **cannot be 100% free** like Gmail was. Honest summary:

| Capability | Recommended provider | Cost reality |
|---|---|---|
| **Email (done)** | Gmail API | Free (consumer cap ~500/day). ✅ |
| **WhatsApp (official)** | Meta WhatsApp Business **Cloud API** (direct) or via BSP (360dialog/Twilio) | Per-conversation pricing (some free service convos/month); needs Meta Business verification + a dedicated number + template approval. Direct Cloud API has **no monthly platform fee**; you pay Meta per conversation (often a few cents). |
| **VoIP calling** | ✅ **Vonage** Voice API + Client SDK (WebRTC) | Number rental (~€1–5/mo) + per-minute (~€0.01–0.02, varies by country) + recording storage. |
| **Call transcription** | OpenAI **Whisper API** (~$0.006/min) or Deepgram (~$0.004/min) | Pay per audio minute. |
| **AI call summary (optional)** | Claude/GPT | Pay per token (cheap per call). |

> There is **no free, ToS-compliant** way to do official WhatsApp Business
> messaging or recorded/transcribed business telephony. (Unofficial WhatsApp-Web
> automation exists but risks a permanent number ban — **not recommended** for an
> official company number.) Budget is required for B4 + B5. B1–B3 (Contact Hub,
> Inbox, two-way email) can be built with **zero new cost**.

### C5. Phased roadmap (each phase ships independently & keeps parity)
- **Phase 1 — Contact Hub (B1)** · ✅ **DONE (2026-06-05)** · no new cost. Master
  `contacts` + `contact_channels` + `activities` (migration 0014), strict
  identity resolution + idempotent backfill (`app/contacts.py`), `/contacts`
  list + `/contacts/{id}` profile with unified timeline + notes, "Contacts" nav.
  Tested: dedup/merge correct, idempotent, all 16 routes 200, parity intact.
- **Phase 2 — Unified Inbox + Agents + two-way Email (B2 + B3 + agents)** · ✅ **DONE (2026-06-05)** · no new cost.
  `agents`, `conversations`, `messages`, `canned_replies` (migration 0015).
  `/inbox` 3-pane Trengo-style UI (list · thread · contact context); assign,
  internal notes, canned replies, labels, statuses (open/pending/snoozed/closed).
  Two-way email: `app/gmail_inbound.py` polls replies (gmail.readonly scope —
  **reconnect Gmail required**) and threads them; replies sent threaded via
  `threadId` + In-Reply-To. `/inbox/settings` manages teammates + canned replies.
  Inbound auto-creates contacts for unknown senders + logs to contact timeline.
  Tested: threading, dedup, reply/note/assign/status, parser, all routes 200, parity intact.
- **Phase 3 — WhatsApp (B4)** · ✅ **CODE DONE (2026-06-05)** — needs Meta Business
  credentials to switch on. `app/whatsapp.py` (Graph API send text + template,
  webhook verify + X-Hub-Signature-256 check, inbound processing, 24h window),
  reuses `conversations`/`messages` (channel='whatsapp') + `whatsapp_templates`
  (migration 0016). Public `GET/POST /webhooks/whatsapp`. Inbox sends WA text
  (in-window) or approved template (out-of-window); start WA from a contact;
  template registry in `/inbox/settings`. Set `WHATSAPP_*` env vars + register
  the webhook in Meta to activate. Tested: verify, signature, payload builders,
  inbound threading + contact creation, window logic, dedup, status updates, parity.
- **Phase 4 — VoIP calling (B5)** · needs Twilio/Telnyx + budget. Softphone, recording.
- **Phase 5 — Transcription + AI summaries + reporting** · needs transcription budget.
- **Phase 6 — Real-time + roles + reporting** · ✅ **DONE (2026-06-05)** · no new cost.
  Agent login + sessions (`app/auth.py`, signed cookie, PBKDF2) layered on the
  HTTP Basic gate — owner stays admin, teammates get role-limited access.
  Role enforcement (`require_admin_role`) on delete/agent-mgmt/template actions,
  `audit_logs` trail (`/audit`, admin-only), reporting dashboard `/reports`
  (email open/click/unsub, inbox volume + per-agent replies + avg first-response,
  audience rollups), and real-time SSE `/api/stream` + `/api/me` driving a live
  nav unread badge. Migration 0017. Tested: login, role gating, audit, reports, parity.
  (VoIP Phase 4 + transcription Phase 5 intentionally deferred.)

### C6. Environment variables (added per phase)
```
# WhatsApp (Phase 3)
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
# VoIP (Phase 4) — example: Twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_API_KEY=
TWILIO_API_SECRET=
TWILIO_TWIML_APP_SID=
TWILIO_CALLER_ID=        # our official number (ported or verified)
# Transcription (Phase 5)
OPENAI_API_KEY=          # Whisper + summaries  (or DEEPGRAM_API_KEY)
```

### C7. Acceptance criteria (per phase)
- **P1**: every existing customer/KVK/lead/prospect resolves to exactly one
  contact; profile page shows merged channels + full timeline; no duplicate merges.
- **P2**: an inbound email reply appears in `/inbox`, can be assigned, replied to
  (threaded), noted internally, and closed; canned replies work.
- **P3**: send + receive WhatsApp from the official number inside a conversation;
  inbound webhook threads correctly; templates send outside the 24h window.
- **P4**: agent clicks "Call" on a contact, talks in-browser, call logs with
  duration + recording playback.
- **P5**: each recorded call shows an accurate transcript + 2-line AI summary on
  the contact timeline; reporting dashboard shows per-agent volume + response time.

---

## DECISIONS (locked 2026-06-05)
1. **Build order**: ✅ **Free phases first** — Phase 1 (Contact Hub) + Phase 2
   (Inbox + two-way email) now at zero new cost; paid channels after.
2. **Budget**: **Minimal (<€50/mo)** for paid channels → keep WhatsApp/VoIP/
   transcription usage lean; prefer cheapest compliant options; transcription
   on-demand rather than every call if needed to stay under budget.
3. **VoIP provider**: ✅ **Vonage** (Voice API + WebRTC client SDK). Build Phase 4 on Vonage.
4. **Official number**: ✅ **Simplest path** — direct Meta **WhatsApp Cloud API**
   (no BSP markup) for WA; for voice, port/verify the official number into Vonage
   (or verified caller-ID if porting is slow).
5. **Transcription**: default **OpenAI Whisper** (~$0.006/min) with optional short
   AI summary; gate behind budget — can be toggled per-call to control cost.

### Cost-control notes for the <€50/mo target
- WhatsApp Cloud API: Meta gives a number of free service conversations/month;
  use approved templates sparingly. Likely a few € at low volume.
- Vonage: 1 number (~€1–5/mo) + per-minute (~€0.01–0.02). At minimal volume well under budget.
- Whisper: only transcribe calls that matter (toggle), or cap minutes/day.

---
*Generated as the master "command" for the Schild Inc in-house CRM upgrade.
Phases 1–2 are zero-cost and start now. Provider: Vonage (voice), Meta Cloud API (WhatsApp), Whisper (transcription).*
