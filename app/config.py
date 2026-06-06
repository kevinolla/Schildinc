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

    # ── Agent sessions (Phase 6 roles) ─────────────────────────────────────
    # Secret used to sign the agent session cookie. Falls back to the
    # unsubscribe secret so it works out-of-the-box, but set a dedicated one.
    session_secret: str = os.getenv("SESSION_SECRET", "") or os.getenv("UNSUBSCRIBE_SECRET", "change-me-too")
    session_ttl_hours: int = int(os.getenv("SESSION_TTL_HOURS", "168"))  # 7 days


settings = Settings()
