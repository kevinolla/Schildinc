from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_database_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    return value


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    app_name: str = os.getenv("APP_NAME", "Schild Inc CRM MVP")
    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
    database_url: str = _normalize_database_url(os.getenv("DATABASE_URL", "sqlite:///./schildinc.db"))
    admin_username: str = os.getenv("ADMIN_USERNAME", "schild")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    google_places_api_key: str = os.getenv("GOOGLE_PLACES_API_KEY", "")
    # Google Custom Search JSON API — used as primary input for Stage 0
    # snippet-based email extraction. CSE_API_KEY falls back to the Places
    # key (same Cloud project usually). CSE_CX is the Search Engine ID,
    # required (no fallback) — get it from
    # https://programmablesearchengine.google.com/
    google_cse_api_key: str = os.getenv("GOOGLE_CSE_API_KEY", "")
    google_cse_cx: str = os.getenv("GOOGLE_CSE_CX", "")
    # Brave Search API — used as primary Stage 0 source after Google
    # deprecated their "Search the entire web" toggle for CSEs. Sign up at
    # https://api.search.brave.com/  (2000 free queries/month).
    brave_api_key: str = os.getenv("BRAVE_API_KEY", "")
    # Hard ceiling on Brave queries per UTC day — safety net so credit
    # can't be blown overnight. Default 300 ≈ ~$1.50/day at $5/1000.
    brave_daily_limit: int = int(os.getenv("BRAVE_DAILY_LIMIT", "300"))
    stripe_api_key: str = os.getenv("STRIPE_API_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    klaviyo_private_api_key: str = os.getenv("KLAVIYO_PRIVATE_API_KEY", "")
    klaviyo_api_revision: str = os.getenv("KLAVIYO_API_REVISION", "2026-04-15")
    klaviyo_default_list_id: str = os.getenv("KLAVIYO_DEFAULT_LIST_ID", "")
    klaviyo_default_list_name: str = os.getenv("KLAVIYO_DEFAULT_LIST_NAME", "")
    resend_api_key: str = os.getenv("RESEND_API_KEY", "")
    mail_provider: str = os.getenv("MAIL_PROVIDER", "console")
    mail_from: str = os.getenv("MAIL_FROM", "noreply@schildinc.com")
    reply_to_email: str = os.getenv("REPLY_TO_EMAIL", "sales@schildinc.com")
    sender_name: str = os.getenv("SENDER_NAME", "Schild Inc Team")
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_use_tls: bool = _as_bool(os.getenv("SMTP_USE_TLS"), True)
    campaign_active: bool = _as_bool(os.getenv("CAMPAIGN_ACTIVE"), True)
    daily_send_limit: int = int(os.getenv("DAILY_SEND_LIMIT", "25"))
    default_queue_size: int = int(os.getenv("DEFAULT_QUEUE_SIZE", "25"))
    send_window_start: str = os.getenv("SEND_WINDOW_START", "08:00")
    send_window_end: str = os.getenv("SEND_WINDOW_END", "17:30")
    outreach_cooldown_days: int = int(os.getenv("OUTREACH_COOLDOWN_DAYS", "14"))
    preview_contact_count: int = int(os.getenv("PREVIEW_CONTACT_COUNT", "20"))
    playwright_timeout_ms: int = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "6000"))
    auto_contact_discovery_enabled: bool = _as_bool(os.getenv("AUTO_CONTACT_DISCOVERY_ENABLED"), True)
    auto_contact_refresh_days: int = int(os.getenv("AUTO_CONTACT_REFRESH_DAYS", "14"))
    auto_contact_refresh_batch_size: int = int(os.getenv("AUTO_CONTACT_REFRESH_BATCH_SIZE", "20"))
    official_instagram_handle: str = os.getenv("OFFICIAL_INSTAGRAM_HANDLE", "@schildinc")
    official_linkedin_url: str = os.getenv("OFFICIAL_LINKEDIN_URL", "https://www.linkedin.com/company/schild-inc/")
    unsubscribe_secret: str = os.getenv("UNSUBSCRIBE_SECRET", "change-me-too")
    klaviyo_list_id: str = os.getenv("KLAVIYO_LIST_ID", "XHgkXM")
    kvk_auto_enrich_enabled: bool = _as_bool(os.getenv("KVK_AUTO_ENRICH_ENABLED"), True)
    kvk_auto_enrich_batch: int = int(os.getenv("KVK_AUTO_ENRICH_BATCH", "12"))
    kvk_auto_enrich_interval: int = int(os.getenv("KVK_AUTO_ENRICH_INTERVAL", "30"))
    kvk_auto_enrich_workers: int = int(os.getenv("KVK_AUTO_ENRICH_WORKERS", "3"))
    # Facebook Lead Ads auto-sync from the public Google Sheet. Every
    # FB_LEADS_AUTO_SYNC_INTERVAL seconds we re-pull the sheet and
    # idempotently upsert any new rows. Default 15 min.
    fb_leads_auto_sync_enabled: bool = _as_bool(os.getenv("FB_LEADS_AUTO_SYNC_ENABLED"), True)
    fb_leads_auto_sync_interval: int = int(os.getenv("FB_LEADS_AUTO_SYNC_INTERVAL", "900"))
    # Lead sector classifier — runs every N seconds, classifies any
    # facebook_leads row whose classifier_version < CURRENT.
    fb_leads_classifier_enabled: bool = _as_bool(os.getenv("FB_LEADS_CLASSIFIER_ENABLED"), True)
    fb_leads_classifier_interval: int = int(os.getenv("FB_LEADS_CLASSIFIER_INTERVAL", "60"))

    # ── Directory crawler (sector x country jobs, migration 0026) ─────────
    # Always-on daemon that runs CrawlJob rows: localized sector search terms
    # x major cities -> Google Places -> dedupe into prospects -> extract a
    # public email per site. Jobs are created/paused from /crawler.
    crawler_enabled: bool = _as_bool(os.getenv("CRAWLER_ENABLED"), True)
    # How many jobs may run at the same time (the user-visible "3-4 tasks").
    crawler_max_concurrent_jobs: int = int(os.getenv("CRAWLER_MAX_CONCURRENT_JOBS", "4"))
    # Scheduler tick: how often we look for runnable jobs.
    crawler_interval: int = int(os.getenv("CRAWLER_INTERVAL", "10"))
    # Seconds between search queries inside one job (rate-limit courtesy for
    # the free backends: Overpass fair-use + the self-hosted SearXNG).
    crawler_query_spacing: float = float(os.getenv("CRAWLER_QUERY_SPACING", "2"))
    # Max results taken per SearXNG query.
    crawler_places_page_size: int = int(os.getenv("CRAWLER_PLACES_PAGE_SIZE", "20"))
    # Seconds between per-site email-extraction crawls inside one job.
    crawler_extract_spacing: float = float(os.getenv("CRAWLER_EXTRACT_SPACING", "0.5"))
    # OpenStreetMap Overpass API (free, keyless, structured business listings).
    # Comma-separated endpoints tried in order; fair-use rate limits apply.
    crawler_osm_enabled: bool = _as_bool(os.getenv("CRAWLER_OSM_ENABLED"), True)
    crawler_osm_endpoints: str = os.getenv(
        "CRAWLER_OSM_ENDPOINTS",
        "https://overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter",
    )
    # Max elements returned per Overpass query (a country-wide sector sweep
    # can be thousands; the job's max_results still caps what gets stored).
    crawler_osm_limit: int = int(os.getenv("CRAWLER_OSM_LIMIT", "2000"))
    crawler_osm_timeout: int = int(os.getenv("CRAWLER_OSM_TIMEOUT", "90"))

    # ── Gmail email engine ─────────────────────────────────────────────────
    # OAuth2 "Web application" client from Google Cloud Console. The
    # redirect URI registered there MUST equal {APP_BASE_URL}/emails/gmail/callback.
    gmail_client_id: str = os.getenv("GMAIL_CLIENT_ID", "")
    gmail_client_secret: str = os.getenv("GMAIL_CLIENT_SECRET", "")
    # The verified "Send mail as" alias to send FROM (e.g. sales@schildinc.com).
    # Must be configured under Gmail → Settings → Accounts → "Send mail as"
    # for the authorized account. Falls back to the authorized account itself.
    gmail_send_as: str = os.getenv("GMAIL_SEND_AS", "sales@schildinc.com")
    gmail_sender_name: str = os.getenv("GMAIL_SENDER_NAME", "Schild Inc")
    # Signature + legal footer (CAN-SPAM / GDPR require a real identity +
    # physical address + opt-out on commercial mail). Protects deliverability
    # and brand reputation. Override in Railway with your real details.
    sender_title: str = os.getenv("SENDER_TITLE", "Sales Team")
    company_legal_name: str = os.getenv("COMPANY_LEGAL_NAME", "Schild Inc")
    company_address: str = os.getenv("COMPANY_ADDRESS", "Schild Inc, Netherlands")
    company_phone: str = os.getenv("COMPANY_PHONE", "")
    company_website: str = os.getenv("COMPANY_WEBSITE", "https://schildinc.com")
    # Daily send ceiling. Gmail's hard cap is ~500/day (consumer) / 2000
    # (Workspace), but a NEW sending identity should ramp up gradually to
    # protect deliverability. Start at 80/day and raise weekly once your
    # open rate looks healthy (e.g. 80 -> 150 -> 250 -> 400).
    gmail_daily_limit: int = int(os.getenv("GMAIL_DAILY_LIMIT", "80"))
    # Seconds between sends inside a campaign (throttle to look human + avoid
    # rate spikes). 8s ≈ 450/hour, well within Gmail's per-minute limits.
    gmail_send_spacing_seconds: float = float(os.getenv("GMAIL_SEND_SPACING_SECONDS", "8"))
    # Background campaign sender daemon — drains scheduled/sending campaigns.
    email_sender_enabled: bool = _as_bool(os.getenv("EMAIL_SENDER_ENABLED"), True)
    email_sender_interval: int = int(os.getenv("EMAIL_SENDER_INTERVAL", "60"))
    # Two-way email: poll the connected Gmail inbox for replies and thread them
    # into the shared inbox. Requires the gmail.readonly scope (reconnect Gmail).
    gmail_inbound_enabled: bool = _as_bool(os.getenv("GMAIL_INBOUND_ENABLED"), True)
    gmail_inbound_interval: int = int(os.getenv("GMAIL_INBOUND_INTERVAL", "120"))
    # On first poll, how many days back to look for replies.
    gmail_inbound_lookback_days: int = int(os.getenv("GMAIL_INBOUND_LOOKBACK_DAYS", "3"))

    # ── Non-Google discovery stack (additive; unset => disabled/no-op) ──────
    # SearXNG self-hosted meta-search for business-name -> website.
    searxng_url: str = os.getenv("SEARXNG_URL", "")
    searxng_engines: str = os.getenv("SEARXNG_ENGINES", "google,bing,duckduckgo,brave")
    searxng_timeout: float = float(os.getenv("SEARXNG_TIMEOUT_S", os.getenv("SEARXNG_TIMEOUT", "8")))
    searxng_timeout_s: float = float(os.getenv("SEARXNG_TIMEOUT_S", os.getenv("SEARXNG_TIMEOUT", "8")))
    # Open geocoding (Photon/Pelias). Never public Nominatim for bulk.
    geocoder_provider: str = os.getenv("GEOCODER_PROVIDER", os.getenv("GEOCODER_KIND", "photon"))
    geocoder_url: str = os.getenv("GEOCODER_URL", "")
    geocoder_timeout_s: float = float(os.getenv("GEOCODER_TIMEOUT_S", "8"))
    pelias_api_key: str = os.getenv("PELIAS_API_KEY", "")
    # Discovery orchestrator. "open" = SearXNG+crawl (default); "google" = legacy fallback path.
    discovery_engine: str = os.getenv("DISCOVERY_ENGINE", os.getenv("DISCOVERY_BACKEND", "open"))
    discovery_review_threshold: int = int(os.getenv("DISCOVERY_REVIEW_THRESHOLD", "60"))
    discovery_autopick_score: int = int(os.getenv("DISCOVERY_AUTOPICK_SCORE", "80"))
    # Use a real browser (Playwright) for JS-heavy pages during extraction.
    web_extract_use_playwright: bool = _as_bool(os.getenv("WEB_EXTRACT_USE_PLAYWRIGHT"), False)
    # Fuzzy customer-suppression thresholds (RapidFuzz).
    suppression_fuzzy_threshold: int = int(os.getenv("SUPPRESSION_FUZZY_THRESHOLD", "88"))
    suppression_fuzzy_medium_threshold: int = int(os.getenv("SUPPRESSION_FUZZY_MEDIUM_THRESHOLD", "80"))

    # ── Email provider abstraction (mail_provider/resend/smtp/reply_to already exist) ──
    # Brevo SMTP relay (bigger free daily cap).
    brevo_smtp_user: str = os.getenv("BREVO_SMTP_USER", "")
    brevo_smtp_key: str = os.getenv("BREVO_SMTP_KEY", "")
    brevo_smtp_host: str = os.getenv("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
    brevo_smtp_port: int = int(os.getenv("BREVO_SMTP_PORT", "587"))
    # Gmail/Workspace SMTP — LOW-VOLUME manual tests only (use an App Password).
    gmail_smtp_user: str = os.getenv("GMAIL_SMTP_USER", "")
    gmail_smtp_app_password: str = os.getenv("GMAIL_SMTP_APP_PASSWORD", "")
    gmail_smtp_host: str = os.getenv("GMAIL_SMTP_HOST", "smtp.gmail.com")
    gmail_smtp_port: int = int(os.getenv("GMAIL_SMTP_PORT", "587"))
    # Optional Redis/RQ worker (off by default; threaded fallback otherwise).
    discovery_use_rq: bool = _as_bool(os.getenv("DISCOVERY_USE_RQ"), False)
    redis_url: str = os.getenv("REDIS_URL", "")

    # ── DESIGN_V2 foundation phase (additive, gated, safe-by-default) ───────
    # Migration 0022. With these at their defaults, production sending behaves
    # exactly as before EXCEPT that NEWLY created campaigns start in dry-run
    # (render-only, never send) until the operator explicitly turns it off.
    # Existing campaigns are backfilled to dry_run=FALSE by the migration, so
    # nothing already created/in-flight is affected.
    #
    # campaign_dry_run_default: the dry_run value stamped on campaigns created
    #   via the UI. TRUE = safe default (a new campaign cannot send real mail
    #   until a human turns dry-run off). Set CAMPAIGN_DRY_RUN_DEFAULT=false to
    #   restore the old "new campaign is immediately sendable" behaviour.
    campaign_dry_run_default: bool = _as_bool(os.getenv("CAMPAIGN_DRY_RUN_DEFAULT"), True)
    # discovery_facts_enabled: gate for the (future) enrichment-fact extraction
    #   callers. OFF by default — the enrichment_facts table + persist helper
    #   exist, but nothing writes to them until this is turned on.
    discovery_facts_enabled: bool = _as_bool(os.getenv("DISCOVERY_FACTS_ENABLED"), False)
    # fact_autotrust_min: a discovered fact with confidence >= this is usable
    #   automatically; below it, review_required=TRUE so it sits in the queue
    #   and is never treated as truth / never auto-used in outreach.
    fact_autotrust_min: int = int(os.getenv("FACT_AUTOTRUST_MIN", "80"))

    # ── DESIGN_V2 Phase 2 (additive, gated, precision-preserving) ──────────
    # A. Discovery recall. When ON, discovery issues several query variants per
    #   company (name; name+city; name+sector clues) and merges candidate
    #   domains before scoring. The ACCEPTANCE gate (_fuzzy_score distinctive-
    #   token + autopick threshold) is UNCHANGED — more candidates only give the
    #   real domain more chances to surface; they can never lower the bar. OFF by
    #   default because it multiplies SearXNG query volume (operational change).
    discovery_recall_variants_enabled: bool = _as_bool(os.getenv("DISCOVERY_RECALL_VARIANTS_ENABLED"), False)
    discovery_max_query_variants: int = int(os.getenv("DISCOVERY_MAX_QUERY_VARIANTS", "6"))
    discovery_variant_search_limit: int = int(os.getenv("DISCOVERY_VARIANT_SEARCH_LIMIT", "5"))
    discovery_max_candidates: int = int(os.getenv("DISCOVERY_MAX_CANDIDATES", "8"))
    # B. enrichment_facts extraction caps (only run when DISCOVERY_FACTS_ENABLED).
    fact_extract_max_pages: int = int(os.getenv("FACT_EXTRACT_MAX_PAGES", "2"))
    # C. Lead scoring. When ON, discovery computes an explainable score per
    #   company (store_quality / commercial_potential / outreach_priority /
    #   sample_pack / call_followup) into the lead_scores table. Never approves
    #   outreach — it only prioritizes. OFF by default.
    lead_scoring_enabled: bool = _as_bool(os.getenv("LEAD_SCORING_ENABLED"), False)
    lead_scoring_engine_version: int = int(os.getenv("LEAD_SCORING_ENGINE_VERSION", "1"))

    # ── DESIGN_V2 Phase 3A: AI-assisted personalization (gated, OFF) ───────
    # Generates first-line / angle / CTA / internal sales note from ONLY trusted
    # facts + accepted website + bike tier + lead score + company data. Never
    # fabricates facts (hallucination guard), never auto-approves outreach, and
    # falls back to safe generic copy when signals are weak or anything fails.
    # OFF by default and a no-op without ANTHROPIC_API_KEY.
    personalization_enabled: bool = _as_bool(os.getenv("PERSONALIZATION_ENABLED"), False)
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    # Two-tier model routing: cheap for bulk, stronger for high-value leads.
    personalization_model_bulk: str = os.getenv("PERSONALIZATION_MODEL_BULK", "claude-haiku-4-5-20251001")
    personalization_model_highvalue: str = os.getenv("PERSONALIZATION_MODEL_HIGHVALUE", "claude-opus-4-8")
    # Below this 0-100 confidence we DISCARD the AI output and ship generic copy.
    personalization_min_confidence: int = int(os.getenv("PERSONALIZATION_MIN_CONFIDENCE", "60"))
    personalization_max_facts: int = int(os.getenv("PERSONALIZATION_MAX_FACTS", "3"))
    # UTC-day call cap (cost breaker, mirrors the Brave breaker pattern).
    personalization_daily_limit: int = int(os.getenv("PERSONALIZATION_DAILY_LIMIT", "200"))

    # ── DESIGN_V2 Phase 3B: 3-step cold email SEQUENCE engine (gated, OFF) ──
    # A weekly cadence (default: Wednesday 07:00 lead-local) of 3 baseline
    # emails with optional layered personalization. Built on TOP of the existing
    # campaign sender (producer pattern): the scheduler materializes campaigns
    # and the unchanged sender drains them, so suppression/tracking/throttle/
    # dry-run all still apply. OFF by default — no enrollments, no scheduler work.
    sequence_engine_enabled: bool = _as_bool(os.getenv("SEQUENCE_ENGINE_ENABLED"), False)
    # Default cadence (Mon=0 … Sun=6). Wednesday=2, 07:00 local, +7 days/step.
    sequence_send_weekday: int = int(os.getenv("SEQUENCE_SEND_WEEKDAY", "2"))
    sequence_send_hour_local: int = int(os.getenv("SEQUENCE_SEND_HOUR_LOCAL", "7"))
    sequence_step_gap_days: int = int(os.getenv("SEQUENCE_STEP_GAP_DAYS", "7"))
    sequence_default_timezone: str = os.getenv("SEQUENCE_DEFAULT_TIMEZONE", "Europe/Amsterdam")
    # Background scheduler tick (seconds) — only runs when the engine is enabled.
    sequence_scheduler_interval: int = int(os.getenv("SEQUENCE_SCHEDULER_INTERVAL", "300"))
    # Re-seed marker for the 3 baseline templates (bump to force re-seed).
    sequence_seed_version: int = int(os.getenv("SEQUENCE_SEED_VERSION", "2"))

    # ── WhatsApp Business Cloud API (direct Meta) ──────────────────────────
    # Set these from Meta → WhatsApp → API setup. The webhook callback URL to
    # register in Meta is {APP_BASE_URL}/webhooks/whatsapp with the verify token.
    whatsapp_phone_number_id: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    whatsapp_business_account_id: str = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    whatsapp_access_token: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    whatsapp_verify_token: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    whatsapp_app_secret: str = os.getenv("WHATSAPP_APP_SECRET", "")
    whatsapp_api_version: str = os.getenv("WHATSAPP_API_VERSION", "v21.0")
    whatsapp_default_lang: str = os.getenv("WHATSAPP_DEFAULT_LANG", "en")

    # ── Instagram Messaging (official Meta Graph API) ──────────────────────
    # Inbound DMs + replies within the 24h window only (Meta does NOT permit
    # cold/proactive DMs via the API). Needs an IG Business account linked to a
    # Facebook Page. Webhook callback: {APP_BASE_URL}/webhooks/instagram.
    instagram_account_id: str = os.getenv("INSTAGRAM_ACCOUNT_ID", "")
    instagram_access_token: str = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    instagram_verify_token: str = os.getenv("INSTAGRAM_VERIFY_TOKEN", "")
    instagram_app_secret: str = os.getenv("INSTAGRAM_APP_SECRET", "")
    instagram_api_version: str = os.getenv("INSTAGRAM_API_VERSION", "v21.0")

    # ── Agent sessions (Phase 6 roles) ─────────────────────────────────────
    # Secret used to sign the agent session cookie. Falls back to the
    # unsubscribe secret so it works out-of-the-box, but set a dedicated one.
    session_secret: str = os.getenv("SESSION_SECRET", "") or os.getenv("UNSUBSCRIBE_SECRET", "change-me-too")
    session_ttl_hours: int = int(os.getenv("SESSION_TTL_HOURS", "168"))  # 7 days


settings = Settings()
