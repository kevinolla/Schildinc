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
    stripe_api_key: str = os.getenv("STRIPE_API_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
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
    playwright_timeout_ms: int = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "12000"))
    auto_contact_discovery_enabled: bool = _as_bool(os.getenv("AUTO_CONTACT_DISCOVERY_ENABLED"), True)
    auto_contact_refresh_days: int = int(os.getenv("AUTO_CONTACT_REFRESH_DAYS", "14"))
    auto_contact_refresh_batch_size: int = int(os.getenv("AUTO_CONTACT_REFRESH_BATCH_SIZE", "20"))
    official_instagram_handle: str = os.getenv("OFFICIAL_INSTAGRAM_HANDLE", "@schildinc")
    official_linkedin_url: str = os.getenv("OFFICIAL_LINKEDIN_URL", "https://www.linkedin.com/company/schild-inc/")
    unsubscribe_secret: str = os.getenv("UNSUBSCRIBE_SECRET", "change-me-too")


settings = Settings()
