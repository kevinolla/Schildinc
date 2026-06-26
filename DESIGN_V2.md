# Schild Inc Outbound Platform — DESIGN V2 (v-Next Redesign)

*Status: implementation-ready. Supersedes per-layer drafts. Critic Critical/High issues resolved inline.*

---

## Executive Summary (for the owner)

Today your tool finds Dutch bike shops, enriches them, and sends cold emails. The next version turns that pipeline into a smart, mostly-automated sales engine — while keeping a human (you) in control of everything that actually leaves the building.

**The five things it adds:**

1. **Finds more real shops.** Smarter web searching reaches businesses we currently miss, without ever guessing wrong and emailing the wrong company.
2. **Scores every lead.** Each shop gets a clear quality score and a "what to do next" label, so you spend time on the best prospects first.
3. **Writes a personal opening line** for each email using AI — based only on *verified* facts about that shop. If the AI isn't confident, it quietly uses your normal template instead. Nothing risky goes out.
4. **Shows the shop their own logo on a Schild metal label** — a mockup image and a personal web page made just for them. This is the single most powerful artifact in cold outreach, and it's the centerpiece.
5. **Follows up automatically.** If someone opens, clicks, or shows interest, the system advances them through a smart follow-up sequence and drops a task on your "My Day" list — but it stops instantly the moment they reply or unsubscribe.

**The promise we never break.** Your live email sending, your "don't email existing customers" protection, your shared inbox, and your open/click tracking are treated as sacred. Every new feature is built *around* them, ships switched **off** by default, and is tested in a safe "dry-run" mode (renders the email but sends nothing) before you ever flip it on. A new campaign cannot accidentally blast anyone — it is harmless until you explicitly approve and send.

**How it rolls out.** In safe stages, hardest-to-break things proven first: (1) safety nets + dry-run, (2) better lead-finding and scoring, (3) AI personalization, (4) logo mockups + landing pages, (5) automated follow-up, (6) retargeting ads — each switched on only after the previous one is solid, **one feature flag per deploy**. Every step is reversible.

**What stays your decision.** Approving a lead for outreach, confirming an AI-written line, approving a mockup, and hitting send — all remain one human click. The machine prepares; you decide.

---

## The 14 Deliverables

### (1) Updated Architecture Proposal

The system stays a **single FastAPI + SQLAlchemy + Jinja monolith on Railway**. No microservices, no message broker, no separate worker fleet. We add a thin tier of **internal service modules** governed by three rules already proven in the codebase:

1. **Pure core, caller persists.** Decision logic (scoring, discovery grading, personalization, mockup compositing, fact extraction) lives in pure functions/dataclasses that take inputs and return results without ORM writes — mirroring `discovery_open.discover_for_company()`. Orchestration commits. Everything new is unit-testable offline.
2. **Daemons are producers; the existing sender is the sole sender.** New background work reuses the module-level `_scheduler_started` + `threading.Lock()` + `try/except` daemon pattern in `lifespan()`. The sequence and CRM daemons **never call a provider** — they materialize `EmailCampaign(status="sending")` rows and hand off to the unchanged `email_engine` sender daemon, so `gmail_daily_limit`, spacing, forced Reply-To, and provider abstraction govern *all* output through one throttle.
3. **Everything new is gated.** Each capability has a `Settings` flag defaulting OFF/safe; unset = production behaves exactly as today.

**Protected subsystems (immovable):** the live send loop (`email_engine.send_campaign_batch`), suppression (**`emailing.is_suppressed`** for the per-recipient gate + `suppression.match_existing_customer` for customer matching/persistence — see critic fix #3), shared inbox (`inbox.add_inbound_message`, provider-keyed threading), tracking (`/e/o`, `/e/c`, `/e/u`, `tracking_token`).

```
┌──────────────────── Railway container ────────────────────┐
│ FastAPI app (app/main.py)                                  │
│  authed pages: /kvk /contacts /inbox /emails /review/*     │
│   /reports /audit + /review/{facts,score,mockups} /landing │
│   /crm/* /sequences/*                                       │
│  public (no Depends(require_admin)): /l/{token} /e/* /webhooks│
├────────────────────────────────────────────────────────────┤
│ PURE SERVICE MODULES (no ORM writes; caller commits)       │
│  enrichment_facts · lead_scoring(+_ai) · personalization · │
│  logo_extract · logo_pipeline · landing · lead_events ·    │
│  sequences · crm_actions                                    │
├────────────────────────────────────────────────────────────┤
│ DAEMONS in lifespan() — idempotent, gated, share one throttle│
│  [existing] kvk_enrich · email_sender(SOLE SENDER) ·       │
│             gmail_inbound · fb_sync · lead_classifier       │
│  [new] lead_scoring · sequences(producer) · crm_actions ·  │
│        mockup_engine · personalization(producer)           │
├────────────────────────────────────────────────────────────┤
│ Postgres (additive 0022–0029) │ Cloudflare R2 (asset bytes)│
└────────────────────────────────────────────────────────────┘
   ext: Anthropic API · SearXNG/Brave · R2 · Klaviyo/Meta (export)
```

**Async/queue strategy:** the "queue" is the database. Daemons poll due rows on 120–300s intervals, batch small, and use **unique constraints as the idempotency guard** (the proven `outreach_queue_items(prospect_id, queue_date)` pattern). The Anthropic **Batches API** (50% cheaper) handles personalization backfill; live calls are reserved for on-demand previews.

### (2) Improved Module Map (Capability → Reused | New | Changed)

| Capability | Reused | New | Changed (additive) |
|---|---|---|---|
| Recall discovery | `search_client`,`web_extract`,`matching`(read) | — | `discovery_open`,`search_client`,`config` |
| Enrichment facts | `enrichment_open`,`lead_classifier`(pattern) | `enrichment_facts` | `models`,`web_extract`,`email_engine`(read),`main`,`config` |
| Lead scoring | `tiering`,`contacts`,`audit`,`auth` | `lead_scoring`,`lead_scoring_ai` | `models`,`main`,`enrichment_open`/`tiering`/`matching` hooks,`config` |
| Personalization | `email_library`,`render_for_recipient`,`audit` | `personalization` | `models`,`email_engine`(1 cond.),`main`,`config` |
| Logo + mockups | `web_extract`,`enrichment_open`,`audit` | `logo_extract`,`logo_pipeline`,`object_store`,`mockup_engine` | `models`,`web_extract`,`enrichment_open`,`email_engine`,`main`,`config`,`requirements` |
| Landing pages | `email_engine` URL builders,`contacts`,`inbox` | `landing` | `models`,`email_engine`(CTA rewrite),`main`,`config` |
| Event spine | `record_open/click/unsub`,`contacts._Resolver`,`gmail_inbound` | `lead_events` | `models`,`email_engine`(3 lines),`gmail_inbound`(1 line),`main`,`config` |
| Sequences | `build_recipients`,sender daemon,`emailing.is_suppressed`,`audit` | `sequences` | `models`,`gmail_inbound`(reply hook),`main`,`config` |
| CRM actions | `contacts`,`inbox`(read),`audit`,`klaviyo_sync` | `crm_actions` | `models`,`enrichment_open`,`main`,`config` |
| UI workspace v2 | templates,`styles.css` | `ui_hints.py`,`_ui.html`,`_review_queue.html`,`email_preview.html`,`bulk.js` | `base.html`,many templates,`styles.css`,`main` |
| Dry-run/testing | four protected subsystems | `tests/conftest.py`,`scripts/seed_demo.py` | `email_engine`(dry-run branch),`config`,`email_campaigns.dry_run` |

**Never changed (behavior):** `email_providers.send`, `gmail_sender`, `matching.apply_kvk_matching`, `emailing.is_suppressed` logic, `suppression` write paths, `inbox` threading, the three `/e/*` endpoints, `klaviyo_sync` semantics.

> **Critic fix #3 (CRITICAL):** `is_suppressed` lives in **`app/emailing.py`**, not `app/suppression.py`. `suppression.py` holds `match_existing_customer` + persistence only. Both modules are frozen choke points; neither may be moved or consolidated. A regression test asserts `send_campaign_batch` calls `app.emailing.is_suppressed` (mock-and-assert-called) so the choke point cannot silently relocate.

### (3) Schema Changes

Single linear migration sequence with **slug revision IDs matching the existing `NNNN_slug` convention** and explicit string `down_revision` pointing at the prior slug (critic fix #1).

| Rev (slug) | down_revision | Adds | Gated by |
|---|---|---|---|
| `0022_dry_run_and_facts` | `0021_open_discovery` | `email_campaigns.dry_run`, `email_campaign_recipients.dry_run_preview_html`; `enrichment_facts`; prospect/contact summary cols | `campaign_dry_run_default`,`discovery_facts_enabled` |
| `0023_lead_scores` | `0022_dry_run_and_facts` | `lead_scores` + denorm cols | `lead_scoring_enabled` |
| `0024_personalization` | `0023_lead_scores` | `personalizations` + `recipient.personalization_id`; `llm_usage_ledger` | `PERSONALIZATION_ENABLED` |
| `0025_decision_makers` | `0024_personalization` | `decision_makers` (polymorphic — see fix #9) | CRM flag |
| `0026_creative_assets` | `0025_decision_makers` | `logo_assets`,`mockup_assets` + parent cols | `mockup_gen_enabled` |
| `0027_landing_pages` | `0026_creative_assets` | `landing_pages`,`landing_page_events` (`/l/{token}` only — fix #4) | `landing_pages_enabled` |
| `0028_events_and_sequences` | `0027_landing_pages` | `lead_events` + Contact signal cols; `sequences`,`sequence_steps`,`sequence_states`; `email_campaigns.sequence_enrollment_id` | `sequence_engine_enabled` |
| `0029_crm_and_retargeting` | `0028_events_and_sequences` | Contact CRM cols; `crm_tasks`; `sample_pack_actions`; `retargeting_segments`(+members) | `crm_action_engine_enabled`,`retargeting_enabled` |

All migrations additive/nullable/server-defaulted with clean drop-only `downgrade()`. Polymorphic `subject_type/subject_id` (no cross-table FKs), mirroring `Activity`.

> **Critic fix #2 (CRITICAL — dry-run backfill):** `0022` sets the *server-default* of `email_campaigns.dry_run` to TRUE for **new** rows, but **backfills all existing rows to `dry_run=FALSE`** to preserve current behavior for already-created/in-flight campaigns. Only the create-campaign route writes `settings.campaign_dry_run_default`. The dry-run short-circuit additionally guards `dry_run=True AND status != 'sending'` so a campaign mid-send can never be silently halted.

> **Critic fix #7 (MEDIUM — facts auto-clear):** `persist_facts` sets `review_required = (confidence < settings.fact_autotrust_min)`. High-confidence facts become merge-usable automatically; their `source`+`confidence` audit trail *is* the review artifact. Only sub-threshold facts sit in the human queue.

> **Critic fix #9 (MEDIUM — decision_makers):** the **polymorphic** schema is canonical: `contact_id` nullable + `company_subject_type`/`company_subject_id` + `source`+`source_url`+`confidence`+`review_status`. The CRM layer references this one table; the earlier contact-required variant is dropped.

> **Critic fix #6 (HIGH — Postgres-specific features):** partial unique indexes (`WHERE is_winner`, `WHERE is_current`, `WHERE status='active'`) and `JSON` (not `JSONB`) are used. Integrity tests for these run against a **disposable Postgres** (testcontainers/Railway clone), not the in-memory SQLite fixture; single-winner is *also* enforced in the writer layer with a unit test so logic is covered even where SQLite diverges.

### (4) Event Flow

```
IMPORT → DISCOVERY/ENRICHMENT (variants→consensus→_fuzzy_score gate→_validate_website firewall)
  → enrichment_facts (+decision_makers, +mockup enqueue)
  → SUPPRESSION REVIEW (exact-only → already_client_flag)
  → SCORING (lead_scores; client/Low-Fit → exclude; low-conf → manual_review)
  → ASSET GEN (logo→clean→quality→composite→R2; all-high → approved else /review/mockups)
  → PERSONALIZATION (gated, cached, budget-capped; <min OR cites unverified → needs_review)
  → LANDING PAGE (snapshot merge_data; auto-publish only if conf≥min)
  → SEND (operator dry_run=False; suppression re-check; tracking_token minted; CTA → /l/{token})
  → EVENT (open/click/landing/cta → record_lead_event → score Δ → segment/NBA)
  → SEQUENCE (on_event: hard-stops first; branch accessory/label/sample; producer → sender handoff)
  → CRM TASK (rules → _upsert_task idempotent → My Day, human deep-links)
  → RETARGETING (refresh_segment excl. do_not_contact+customers → Klaviyo/Meta EXPORT ONLY)
```

The loop closes: a sequence-produced send flows back through tracking → `record_lead_event` → `on_event`, attributed via `email_campaigns.sequence_enrollment_id`. **Landing/CTA events flow through `record_lead_event` exactly once** (fix #5): `lead_events` is the single sales-signal spine; `landing_page_events` holds raw landing telemetry only, and the fold-into-`EmailCampaignRecipient` happens in one place inside `record_lead_event`.

### (5) Lead Lifecycle

Two orthogonal tracks: pipeline **stage** (`Contact.stage`) and per-asset **gate states** (`review_required`).

```
IMPORTED → ENRICHING → ENRICHED
  ├ exact match → SUPPRESSED_CLIENT (terminal-for-cold)
  ├ score → SCORED ─ exclude/LowFit → EXCLUDED; low-conf → MANUAL_REVIEW
  └ eligible → READY_FOR_REVIEW ── owner click ──► APPROVED_FOR_OUTREACH
        → ENROLLED → CONTACTED → ENGAGED → INTERESTED → SAMPLE_SENT → APPOINTMENT → WON/LOST
  HARD STOPS from any active state: reply→RESPONDED | unsubscribe→UNSUBSCRIBED(+SuppressionEntry) | booking→APPOINTMENT
```

**Invariant:** the only edge into a real send is `APPROVED_FOR_OUTREACH` (human) → `ENROLLED`/campaign → `dry_run=False` (human). No automated transition can send.

### (6) Personalization Rules

Two-tier routing: `claude-haiku-4-5` for high-volume first-lines; `claude-opus-4-8` for high-value/complex leads (Good Tier, high LTV, ≥2 verified facts). Frozen system prompt (prompt-cached), per-lead facts in the volatile user turn, structured JSON output. Facts labeled VERIFIED vs UNVERIFIED; the prompt forbids stating unverified facts as truth. `_grade` sets confidence = min(fact source confidence, model self-rating) with a **hallucination guard**: any `facts_used` entry absent from the VERIFIED bundle → confidence 0, `needs_review`, drop first-line. Idempotency via `UNIQUE(source_kind, source_id, fact_fingerprint)`. UTC-day budget breaker (`llm_max_cost_usd_day`) modeled on the Brave circuit-breaker. **Fail-open:** any error/over-budget/low-confidence/unreviewed → `{{personalization}}` resolves to `""` and the generic template ships unchanged. Runs at draft time, never in the send loop.

### (7) Scoring Model

Six explainable dimensions per KVK/Prospect (deterministic rules engine, pure functions): Store Quality (0–100), Commercial Potential, Outreach Priority, Bike Tier (mirrored read-only copy of `bike_shop_tier`), Sample Pack Eligibility, Call Follow-up Eligibility. `already_client_flag` → forced `exclude`; `Low Fit` → `exclude`; `Brand Store`/`Hard to Reach` → `manual_review`; confidence below threshold → `manual_review`. Optional AI assist merges **downward only** (`max(deterministic_restrictiveness, ai_restrictiveness)`); it can never raise priority or flip `exclude`. Recompute via `engine_version` bump or `input_fingerprint` change; `manual_override=True` freezes the row. Scoring **never** sets `approved_for_outreach`.

### (8) Mockup Pipeline

`logo_extract` (pure HTML parse of already-fetched home page — JSON-LD `Organization.logo`, `og:image`, header `<img class=logo>`, favicon, ranked by confidence) → `logo_pipeline` (Pillow: fetch, RGBA, autocrop, quality score, composite onto `app/static/mockup_templates/{variant}.png` with a JSON bbox sidecar) → `object_store.put` → R2. Auto-approve only when `logo_quality_score`, `logo_confidence`, and `composite_score` all clear the high threshold; else `/review/mockups`. Two new tables (`logo_assets`, `mockup_assets`) store only `storage_key`+`public_url`; bytes never on Railway FS.

### (9) Landing Page System

**Single canonical design: `/l/{token}`** (critic fix #4 — the `/p/{slug}` variant is deleted). New public route (omits `Depends(require_admin)`) renders a per-recipient page from a snapshot of `merge_data` taken at campaign build (never re-queries source rows). Mockup framed as "your brand" only when `mockup_confidence ≥ landing_mockup_confidence_min`, else generic fallback. CTAs route through `/l/{token}/cta?c=&u=` which calls `record_lead_event` (folding the click into `EmailCampaignRecipient` once) then redirects. Auto-publish only when `confidence ≥ landing_auto_publish_min`. Sample-request/reply CTAs create *inbound* records via `inbox`/`contacts` — never an outbound send.

### (10) CRM Action Engine

State on `Contact` (stage, next_action, sample/appointment status, engagement_score). A watermark-based daemon reads `EmailEvent`/`Message`/`lead_events`, scores engagement, and calls `_upsert_task` (unique `contact_id+action_type+dedupe_key` → idempotent). Action types deep-link to the existing human-driven inbox/campaign flows — **the engine never calls a provider**. Decision-makers from enrichment are quarantined (`review_required`) until human-approved before any merge use. Retargeting membership excludes `do_not_contact`+customers and is export-only.

### (11) UI/UX Page Plan

`/kvk/{id}` becomes the core workspace: every fact with source + confidence chip + inline action. Shared `_ui.html` macros (`badge`, `tier_badge`, `conf_chip`) replace inline-style soup, mapping to existing `styles.css` classes. `next_action_for()` (in `ui_hints.py`) drives a "Next action" pill on dashboard/list/detail. Sticky bulk-action bar (vanilla `bulk.js`, progressive — degrades to per-row forms). New `/emails/campaigns/{id}/preview` shows email + landing + mockup side-by-side, **read-only** (zero `EmailEvent`/recipient rows, sender mocked-and-asserted-not-called). Bulk approve/campaign actions server-side filter `already_client_flag` + active suppression even if checkboxes are ticked.

### (12) Rollout Plan

Phased, slug-migration-clean, one flag per deploy. See BUILD-FIRST vs LATER below and the Safe-deploy checklist in the appendix.

### (13) Biggest Risks + Mitigation

| Risk | Mitigation |
|---|---|
| Wrong-business autopick | `_fuzzy_score` distinctive-token gate authoritative; variants only add candidates; `_validate_website` can only downgrade; precision tests locked in CI |
| Outreach to customers | strict exact-only match untouched; scoring forces `exclude`; `emailing.is_suppressed` re-checked at build AND send; bulk UI filters server-side |
| Assets on ephemeral FS | R2 only; `test_mockup_storage.py` asserts nothing under `/app/data` |
| LLM cost/latency/hallucination | per-recipient cache, UTC-day budget breaker, fail-open to template, VERIFIED cross-check, HTML-sanitized |
| Low-conf auto-send | `confidence`+`review_required` on every fact/asset; merge → `""` unless reviewed AND ≥ threshold |
| Daemon contention (6→11) | producers share the one send throttle; 120–300s intervals; **flip ONE flag/deploy** (fix #10) |
| Accidental blast | `dry_run` server-default TRUE for new rows; existing rows backfilled FALSE; seed data fake-only |
| Migration collision/rollback | slug IDs + explicit `down_revision`; round-trip test before authoring; drop-only downgrades |
| Retargeting GDPR | `retargeting_min_audience`=300 (k-anon); `gdpr_lawful_basis`+`reviewed_by` required; export-only |

### (14) Build-First vs Later

See dedicated list below.

---

## Appendix — Per-Layer Detail

*(Discovery yield, lead scoring, personalization, mockups, landing, event spine + sequences, CRM action engine, consolidated data model, UI workspace, testing/rollout — full specs retained from the layer designs, with the canonical reconciliations applied: single `is_suppressed` reference to `emailing`; slug migration IDs; dry-run backfill FALSE; single `/l/{token}` landing design; `lead_events` as the single funnel spine with `landing_page_events` as raw telemetry read once; polymorphic `decision_makers`; `review_required` auto-clear at `fact_autotrust_min`; R2 justified by worker contention + durability, not an auth wall.)*

**Safe-deploy checklist (every phase):**
- `pytest -q` green, six regression-lock suites unmodified.
- Migration round-trips on a Postgres clone (`upgrade head` → `downgrade -1` → byte-identical to prior head).
- New flag confirmed OFF in Railway env.
- Golden test: `build_recipients()` + `send_campaign_batch()` identical with migrations applied and no new rows.
- Preview/dry-run routes assert zero `EmailEvent`/recipient rows and never call `send_campaign_batch`.
- Suppression non-bypass test: suppressed/customer → no send, no recipient, sequence exits `suppressed`.
- `railway up`; tail logs; verify daemons start idempotently.
- **Flip exactly ONE capability flag; never enable two new daemons in one deploy.** Watch `/audit` + `/logs`; ramp.

---

## SAFETY MATRIX

| Capability | Reused | New | Changed | Manual vs Automated | Review gate | Deploys without breaking the live sender |
|---|---|---|---|---|---|---|
| Recall discovery | `search_client`,`web_extract`,`matching`(read) | — | `discovery_open`,`search_client`,`config` | Automated candidate gen | Sub-threshold → `/review/discovery` | Upstream of build; writes only KVK/prospect; `_fuzzy_score` gate unchanged; flag off |
| Enrichment facts | `enrichment_open` | `enrichment_facts` | `models`,`web_extract`,`email_engine`(read),`main` | Automated extract | `review_required = conf < fact_autotrust_min` | New table; merge → `""` if unreviewed; no send/suppress edit |
| Lead scoring | `tiering`,`contacts`,`audit` | `lead_scoring`(+ai) | `models`,`main`,hooks | Automated score | `manual_review` queue; override audited | Side table; never sets `approved_for_outreach`; reads `already_client_flag` |
| Personalization | `email_library`,`render_for_recipient`,`audit` | `personalization` | `models`,`email_engine`(1 cond.),`main` | Automated draft | `<min conf` → `/emails/personalizations` | Fail-open to generic; one read-conditional; draft-time not send loop |
| Mockups/logos | `web_extract`,`enrichment_open`,`audit` | `logo_extract`,`logo_pipeline`,`object_store`,`mockup_engine` | `models`,`email_engine`(merge field),`main`,`requirements` | Automated render | `/review/mockups` unless all-high | Bytes on R2; `{{mockup_image_url}}` empty unless approved; public URL |
| Landing pages | `email_engine` URL builders,`contacts`,`inbox` | `landing` | `models`,`email_engine`(CTA rewrite),`main` | Automated build | auto-publish only if conf≥min | Downstream of click; never sends; sample/reply CTAs are inbound-only |
| Event spine | `record_open/click/unsub`,`contacts`,`gmail_inbound` | `lead_events` | `models`,`email_engine`(3 lines),`gmail_inbound`(1 line),`main` | Automated ingest+score | confidence floor blocks branch | Parallel table; one appended line per recorder; `/e/*` untouched |
| Sequences | `build_recipients`,sender daemon,`emailing.is_suppressed`,`audit` | `sequences` | `models`,`gmail_inbound`(reply hook),`main` | Automated transitions | activation + per-enrollment approval; low-conf → CRM task | **Producer only**: enqueues `status="sending"`; sender stays sole sender; shares throttle |
| CRM actions | `contacts`,`inbox`(read),`audit`,`klaviyo_sync` | `crm_actions` | `models`,`enrichment_open`,`main` | Automated task creation | every send is human deep-link; physical ship confirmed | Never calls a provider; reads events; checks `do_not_contact` first |
| Retargeting | `klaviyo_sync`,suppression(read) | (in `crm_actions`/model) | `models`,`main` | Automated membership | export needs `reviewed_by`+GDPR basis; floor 300 | Export-only; excludes suppressed+customers; no send path |
| UI workspace v2 | templates,`styles.css` | `ui_hints`,`_ui.html`,`_review_queue.html`,`email_preview.html`,`bulk.js` | `base.html`,templates,`main` | Presentational | bulk actions filter suppressed/customer server-side | Read-only renders + thin wrappers; preview never sends |
| Dry-run/testing | four protected subsystems | `conftest.py`,`seed_demo.py` | `email_engine`(branch),`config` | Operator chooses dry-run | dry-run default TRUE (new rows only) | New short-circuit; live path byte-identical; dry-run excluded from `sent_today()` |

---

## BUILD-FIRST vs LATER

**BUILD FIRST — foundation, highest leverage, lowest risk (nothing user-visible activates):**
1. `conftest.py` + regression lock + **dry-run keystone (`0022`)** with `test_email_engine_dryrun.py`. Pure safety; makes every later capability testable with zero real email. Includes the migration round-trip test and the `emailing.is_suppressed`-is-called assertion.
2. **Raised-recall discovery + `enrichment_facts`.** Highest business value (more reachable shops from the existing 3,990 KVK pool), zero send-path risk, reuses the precision gate.
3. **Lead scoring (`0023`).** Cheap, deterministic; prioritizes the review queues; pure side-table.

**BUILD NEXT — high value, contained risk (each flag-gated, flipped one at a time):**
4. **Personalization (`0024`)** — biggest conversion lever; fail-open contains LLM risk; ship dark + dry-run first.
5. **Mockups + landing pages (`0026`, `0027`)** — the "your logo on a Schild label" artifact; gated behind durable R2 storage + approval.
6. **Event spine + sequences (`0028`)** — turns tracking into automated follow-up; producer pattern keeps the sender safe.

**LATER — automation depth + external/compliance surface:**
7. **CRM action engine (`0029` part)** — valuable but depends on event-spine maturity; mostly UI + rules over existing data.
8. **Retargeting export** — last: the only path sending data off-box (GDPR), and it works best fed by real engagement history.

---

## What is reused / new / changes / stays manual / review gates / safe deploy

**Reused unchanged:** `email_engine.send_campaign_batch` send loop, `email_providers.send`, `gmail_sender`, `emailing.is_suppressed`, `suppression.match_existing_customer`, `inbox` threading, `/e/o`/`/e/c`/`/e/u`, `matching.apply_kvk_matching`, `klaviyo_sync`, `tiering.apply_bike_tier`, the 11-sector classifier.

**New modules:** `enrichment_facts`, `lead_scoring`(+`_ai`), `personalization`, `logo_extract`, `logo_pipeline`, `object_store`, `mockup_engine`, `landing`, `lead_events`, `sequences`, `crm_actions`, `ui_hints`; tests `conftest.py`; `scripts/seed_demo.py`.

**Modules that change (additive only):** `models`, `config`, `main` (routes + lifespan daemon registration), `email_engine` (dry-run branch + read-only conditional merge fields + CTA rewrite — never the provider call), `web_extract` (new dataclass fields), `enrichment_open` (post-persist hooks), `gmail_inbound` (one reply-event line), templates + `styles.css`, `requirements` (Pillow, boto3).

**Stays manual (human-gated):** approving a lead for outreach, confirming/editing an AI line, approving a mockup, publishing a low-confidence landing page, confirming a physical sample shipment, approving a decision-maker for merge use, retargeting export, and flipping a campaign to `dry_run=False`.

**Review gates:** `/review/discovery` (sub-threshold sites), `/review/facts`, `/review/score`, `/review/match`, `/review/ready`, `/review/mockups`, `/emails/personalizations`, `/sequences/approvals`, `/crm/decision-makers`.

**Deploy safely without breaking the live sender:** ship every layer dark (flag OFF), run `pytest -q` + migration round-trip on a Postgres clone before `railway up`, keep slug migration IDs with explicit `down_revision`, backfill existing campaigns to `dry_run=FALSE`, verify the golden send-path test is byte-identical, then **flip exactly one flag per deploy** and watch `/audit` + `/logs`. The sequence/CRM daemons only *produce* `EmailCampaign(status="sending")` rows — the unchanged `email_sender` daemon remains the sole provider caller, so `gmail_daily_limit`, spacing, and forced Reply-To govern all output through one throttle.
